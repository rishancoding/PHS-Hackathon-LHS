"""Compare catalog modules using the same budget-constrained layouts shown on the map."""
import csv
import math
import hashlib
import json
from pathlib import Path

from impact import estimate_impact
from installation_cost import estimate_installation_cost
from panel_plan import propose_panels, validate_plan_inputs
from solar_address import InputError

CATALOG = Path(__file__).with_name('data') / 'panel_catalog.csv'
PRIORITIES = {'balanced', 'lowest_cost', 'space_efficiency', 'longer_warranty'}
MAX_BLOCKED_ZONES = 200
# Hobby-size modules (5-60 W) would need hundreds of panels on a roof.
MIN_ROOFTOP_WATTS = 100
# Explicit modeling allowance, not a measured module price or local labor quote.
REFERENCE_MODULE_ALLOWANCE_PER_WATT = .35


def load_catalog(path=CATALOG):
    try:
        with Path(path).open(newline='', encoding='utf-8-sig') as file:
            rows = list(csv.DictReader(file))
    except (OSError, csv.Error) as exc:
        raise InputError('Panel catalog could not be read. Check data/panel_catalog.csv.') from exc
    models, ids = [], set()
    for row in rows:
        model = dict(row)
        try:
            for field in ('width_m', 'height_m', 'rated_power_w', 'price_usd'):
                model[field] = float(row[field])
                if not math.isfinite(model[field]) or model[field] <= 0:
                    raise ValueError(field)
            for field in ('product_warranty_years', 'performance_warranty_years',
                          'temperature_coefficient_pct_per_c', 'weight_kg'):
                model[field] = float(row[field]) if row.get(field, '').strip() else None
                if model[field] is not None and not math.isfinite(model[field]):
                    raise ValueError(field)
            if not row.get('model_id') or row['model_id'] in ids:
                raise ValueError('model_id')
        except (ValueError, TypeError, KeyError) as exc:
            raise InputError(f"Invalid catalog values for {row.get('model_id', 'unnamed model')}.") from exc
        ids.add(model['model_id'])
        model['area_m2'] = model['width_m'] * model['height_m']
        model['power_density_w_per_m2'] = model['rated_power_w'] / model['area_m2']
        model['price_per_watt_usd'] = model['price_usd'] / model['rated_power_w']
        models.append(model)
    if not models:
        raise InputError('Panel catalog has no models.')
    return models


def validate_recommendation_inputs(body):
    values = validate_plan_inputs(body.get('budget_usd', 25000), body.get('energy_target_kwh_year', 10000))
    values.pop('installed_cost_per_watt')
    includes_installation = body.get('budget_includes_installation', True)
    if not isinstance(includes_installation, bool):
        raise InputError('Budget includes installation must be true or false.')
    values['budget_includes_installation'] = includes_installation
    priority = body.get('priority', 'balanced')
    if not isinstance(priority, str) or priority not in PRIORITIES:
        raise InputError('Choose a valid panel recommendation priority.')
    space = body.get('space_limit_m2')
    if space not in (None, ''):
        try:
            space = float(space)
        except (TypeError, ValueError):
            raise InputError('Space limit must be a positive number in square meters.') from None
        if not math.isfinite(space) or space <= 0:
            raise InputError('Space limit must be finite and greater than zero.')
    else:
        space = None
    selected = body.get('selected_model_id')
    if selected is not None and not isinstance(selected, str):
        raise InputError('Selected model must be a catalog model ID.')
    excluded = body.get('excluded_roof_ids')
    if excluded is not None and (not isinstance(excluded, list) or len(excluded) > 10000
                                 or any(not isinstance(value, str) for value in excluded)):
        raise InputError('Excluded roofs must be a list of roof IDs.')
    zones = body.get('blocked_zones')
    if zones is not None:
        if not isinstance(zones, list) or len(zones) > MAX_BLOCKED_ZONES:
            raise InputError(f'No-panel zones must be a list of at most {MAX_BLOCKED_ZONES} boxes.')
        parsed = []
        for zone in zones:
            if (not isinstance(zone, list) or len(zone) != 4
                    or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in zone)):
                raise InputError('Each no-panel zone must be four finite numbers: left, top, right, bottom.')
            left, right = sorted(zone[0::2])
            top, bottom = sorted(zone[1::2])
            if right <= left or bottom <= top:
                raise InputError('No-panel zones need a width and height greater than zero.')
            parsed.append([left, top, right, bottom])
        zones = parsed
    derate = body.get('system_derate')
    if derate not in (None, ''):
        try:
            derate = float(derate)
        except (TypeError, ValueError):
            raise InputError('System efficiency must be a number between 0.5 and 1.') from None
        if isinstance(body.get('system_derate'), bool) or not math.isfinite(derate) or not .5 <= derate <= 1:
            raise InputError('System efficiency must be between 0.5 and 1.')
    else:
        derate = None
    values.update(priority=priority, space_limit_m2=space, selected_model_id=selected, system_derate=derate,
                  excluded_roof_ids=list(dict.fromkeys(excluded)) if excluded is not None else None,
                  blocked_zones=zones)
    return values


def build_recommendations(result, budget_usd=25000, energy_target_kwh_year=10000,
                          priority='balanced', space_limit_m2=None, selected_model_id=None, catalog=None, excluded_roof_ids=None, reuse_comparison=False, budget_includes_installation=True,
                          blocked_zones=None, system_derate=None, sunlight=None):
    inputs = validate_recommendation_inputs(dict(budget_usd=budget_usd, energy_target_kwh_year=energy_target_kwh_year,
                                               priority=priority, space_limit_m2=space_limit_m2,
                                               selected_model_id=selected_model_id, excluded_roof_ids=excluded_roof_ids,
                                               budget_includes_installation=budget_includes_installation,
                                               blocked_zones=blocked_zones, system_derate=system_derate))
    if inputs['system_derate'] is not None:
        result['assumptions']['system_derate'] = inputs['system_derate']
    excluded_roof_ids = inputs['excluded_roof_ids']
    if excluded_roof_ids is None:
        excluded_roof_ids = result.get('roof_selection', {}).get('excluded_roof_ids', [])
    known_roofs = {roof['properties']['id'] for roof in result['roofs']}
    if set(excluded_roof_ids) - known_roofs:
        raise InputError('One or more excluded roof IDs do not belong to this analysis.')
    excluded_roof_ids = sorted(set(excluded_roof_ids))
    # User-drawn no-panel zones, in preview pixels; None keeps the saved zones.
    zone_bounds = inputs['blocked_zones']
    if zone_bounds is None:
        zone_bounds = [zone['bounds'] for zone in result.get('blocked_zones', [])]
    width = result['analysis'].get('raster_width', math.inf)
    height = result['analysis'].get('raster_height', math.inf)
    resolution = result['analysis']['resolution_m']
    zones = []
    for left, top, right, bottom in zone_bounds:
        left, right = max(0, left), min(width, right)
        top, bottom = max(0, top), min(height, bottom)
        if right > left and bottom > top:
            zones.append({'id': f'zone-{len(zones) + 1}', 'bounds': [round(v, 2) for v in (left, top, right, bottom)],
                          'area_m2': round((right - left) * (bottom - top) * resolution ** 2, 1)})
    result['blocked_zones'] = zones
    planning_result = {**result, 'roofs': [roof for roof in result['roofs']
                                        if roof['properties']['id'] not in excluded_roof_ids]}
    models = load_catalog() if catalog is None else catalog
    eligible = [m for m in models if m.get('panel_type') == 'rigid' and m['rated_power_w'] >= MIN_ROOFTOP_WATTS]
    def exclusion(m):
        if m.get('panel_type') == 'portable_foldable':
            return 'Portable/foldable module: not compared as permanent roof hardware.'
        if m.get('panel_type') == 'rigid':
            return f'Under {MIN_ROOFTOP_WATTS} W: too small for a practical rooftop array.'
        return 'Flexible or unclassified module: mounting compatibility needs a separate model.'
    excluded = [{'model_id': m['model_id'], 'model_name': m['model_name'], 'reason': exclusion(m)}
                for m in models if m not in eligible]
    if not eligible:
        raise InputError('Catalog has no rigid modules for rooftop comparison.')
    if selected_model_id and selected_model_id not in {m['model_id'] for m in eligible}:
        raise InputError('Selected panel is not an eligible rigid catalog module.')
    geographic = estimate_installation_cost(result)
    non_module_rate = max(0, geographic['installed_cost_per_watt'] - REFERENCE_MODULE_ALLOWANCE_PER_WATT)
    geometry_cache = {}
    def model_plan(model):
        unit_cost = model['price_usd'] + model['rated_power_w'] * non_module_rate
        plan = propose_panels(planning_result, budget_usd, energy_target_kwh_year,
                              installed_cost_per_watt=unit_cost / model['rated_power_w'],
                              panel_model=model, space_limit_m2=space_limit_m2, _geometry_cache=geometry_cache,
                              budget_includes_installation=budget_includes_installation, sunlight=sunlight)
        plan['installation_estimate'] = {**geographic, 'installed_cost_per_watt': plan['installed_cost_per_watt'],
                                        'low_cost_per_watt': plan['installed_cost_per_watt'] * .75,
                                        'high_cost_per_watt': plan['installed_cost_per_watt'] * 1.25,
                                        'non_module_cost_per_watt': round(non_module_rate, 4),
                                        'reference_module_allowance_per_watt': REFERENCE_MODULE_ALLOWANCE_PER_WATT,
                                        'notes': 'CSV module price plus a geographic allowance for other equipment and installation. '
                                        'Allowance = previous all-in regional benchmark minus an illustrative $0.35/W module allowance; '
                                        'this separation and the ±25% total range are modeling assumptions. '
                                        'Before incentives, excluding batteries, shipping and tax; not a current installer quote.'}
        plan['estimated_cost_range_usd'] = [round(plan['estimated_cost_usd'] * .75, 2), round(plan['estimated_cost_usd'] * 1.25, 2)]
        plan['estimated_panel_hardware_cost_usd'] = round(plan['panel_count'] * model['price_usd'], 2)
        plan['estimated_other_equipment_installation_cost_usd'] = round(plan['estimated_cost_usd'] - plan['estimated_panel_hardware_cost_usd'], 2)
        return plan

    signature_data = [models, inputs['budget_usd'], inputs['energy_target_kwh_year'], space_limit_m2, budget_includes_installation,
                      priority, excluded_roof_ids, geographic, result['analysis']['resolution_m'], result['assumptions'],
                      [(roof['properties']['id'], roof['properties']['area_m2'],
                        roof['properties']['flux_coverage_percent'], roof['properties'].get('annual_yield_kwh_per_kw'),
                        roof.get('preview_geometry')) for roof in result['roofs']],
                      [feature.get('preview_geometry') for feature in result.get('existing_panels', [])],
                      [zone['bounds'] for zone in zones], sunlight is not None]
    signature = hashlib.sha256(json.dumps(signature_data, sort_keys=True, allow_nan=False).encode()).hexdigest()
    previous = result.get('panel_recommendations', {})
    if reuse_comparison and previous.get('comparison_signature') == signature:
        chosen_id = selected_model_id or previous['recommended_model_id']
        if result.get('proposal', {}).get('panel_model', {}).get('model_id') == chosen_id:
            return result
        selected_plan = model_plan(next(model for model in eligible if model['model_id'] == chosen_id))
        apply_selection(result, selected_plan, known_roofs, excluded_roof_ids)
        result['panel_recommendations'] = {**previous, 'selected_model_id': chosen_id}
        return result

    evaluated = []
    for model in eligible:
        plan = model_plan(model)
        tier = 1 if plan['target_met'] else 2 if plan['panel_count'] else 3
        evaluated.append((model, plan, tier))

    def ranking(item):
        model, plan, tier = item
        # Constraints always take precedence over a user preference.
        shortfall = plan['energy_shortfall_kwh_year'] if tier != 1 else 0
        if priority == 'longer_warranty':
            preference = (-(model.get('product_warranty_years') or 0), -(model.get('performance_warranty_years') or 0))
        elif priority == 'space_efficiency':
            preference = (plan['panel_area_used_m2'] if tier == 1 else -model['power_density_w_per_m2'],)
        elif priority == 'lowest_cost':
            preference = (plan['estimated_cost_usd'] if tier == 1 else plan['installed_cost_per_watt'],)
        else:
            preference = (plan['estimated_cost_usd'], plan['panel_area_used_m2'])
        return (tier, shortfall, *preference, plan['estimated_cost_usd'], model['model_id'])

    evaluated.sort(key=ranking)
    best_id = evaluated[0][0]['model_id']
    lowest_rate = min(m['price_per_watt_usd'] for m in eligible)
    highest_density = max(m['power_density_w_per_m2'] for m in eligible)
    longest_product = max(m.get('product_warranty_years') or 0 for m in eligible)
    recommendations = []
    selected_plan = None
    for index, (model, plan, tier) in enumerate(evaluated, 1):
        reasons = [plan['status']]
        if not budget_includes_installation:
            reasons.append('The budget covers module hardware only; other equipment and installation are additional costs.')
        if math.isclose(model['price_per_watt_usd'], lowest_rate):
            reasons.append('Lowest listed hardware cost per watt among eligible models; useful when upfront cost matters.')
        if math.isclose(model['power_density_w_per_m2'], highest_density):
            reasons.append('Highest rated watts per square meter in this rooftop catalog; useful when roof space is tight.')
        product, performance = model.get('product_warranty_years'), model.get('performance_warranty_years')
        if product:
            reasons.append(f"{product:g}-year product warranty" + (' — longest documented product coverage in this catalog.' if product == longest_product else '.'))
        else:
            reasons.append('Product warranty length is not documented in this catalog.')
        if performance:
            reasons.append(f'{performance:g}-year output warranty may matter when planning long-term use; it is not a product-lifetime guarantee.')
        coeff = model.get('temperature_coefficient_pct_per_c')
        if coeff is not None:
            reasons.append(f'Rated power changes {coeff:g}% per °C above STC cell temperature; useful for comparing hot-weather output.')
        if model['rated_power_w'] <= 60:
            reasons.append('Small footprint may fit narrow areas, but many modules and electrical connections are needed for a large energy target.')
        reasons.append('Physical durability and expected service life are unverified; warranty terms are evidence of coverage, not proof of lifespan.')
        summary = {key: plan[key] for key in ('budget_cost_usd', 'budget_includes_installation', 'panel_count', 'estimated_cost_usd', 'estimated_energy_kwh_year',
                   'energy_shortfall_kwh_year', 'target_met', 'panel_area_used_m2', 'available_roof_area_m2',
                   'estimated_cost_range_usd', 'estimated_panel_hardware_cost_usd',
                   'estimated_other_equipment_installation_cost_usd', 'layout_search_limited')}
        recommendations.append({**summary, 'rank': index, 'tier': tier, 'model': model, 'reasons': reasons,
                                'estimated_installed_cost_per_panel_usd': round(model['rated_power_w'] * plan['installed_cost_per_watt'], 2),
                                'recommended': model['model_id'] == best_id})
        if model['model_id'] == (selected_model_id or best_id):
            selected_plan = plan
    apply_selection(result, selected_plan, known_roofs, excluded_roof_ids)
    result['panel_recommendations'] = {
        'comparison_signature': signature, 'budget_includes_installation': budget_includes_installation, 'priority': inputs['priority'], 'budget_usd': budget_usd, 'energy_target_kwh_year': energy_target_kwh_year,
        'space_limit_m2': space_limit_m2, 'recommended_model_id': best_id,
        'selected_model_id': selected_plan['panel_model']['model_id'], 'catalog_count': len(models),
        'recommendations': recommendations, 'excluded_models': excluded,
        'notes': 'Stock and order availability ignored. Tiers use the central installed-cost estimate, roof-average annual yield, '
                 'actual catalog dimensions and 0.2 m gaps. Each alternative uses one model throughout the layout. '
                 'Both axis-aligned orientations are tried; this greedy screening search is not a guaranteed global optimum. '
                 'Unknown degradation is not invented, and bifacial rear gain is not added. Roof-potential rankings retain their separate density assumption.'}
    return result


def placement_reasons(result, plan):
    """Explain actual placements without claiming point-level sunlight optimization."""
    excluded = set(result.get('roof_selection', {}).get('excluded_roof_ids', []))
    eligible = sorted((r for r in result['roofs'] if r['properties']['id'] not in excluded
                       and r['properties'].get('annual_yield_kwh_per_kw')
                       and r.get('preview_geometry')),
                      key=lambda r: r['properties']['annual_yield_kwh_per_kw'], reverse=True)
    order = {r['properties']['id']: i for i, r in enumerate(eligible, 1)}
    reasons = []
    panels_by_roof = {}
    for panel in plan['panels']:
        panels_by_roof.setdefault(panel['roof_id'], []).append(panel)
    for roof in result['roofs']:
        props = roof['properties']; rid = props['id']
        panels = panels_by_roof.get(rid, [])
        if rid in excluded:
            reason = 'Excluded by you; no panels may be placed here.'
        elif panels:
            orientations = sorted({(round(p['width_m'], 3), round(p['height_m'], 3)) for p in panels})
            sizes = ', '.join(f'{width} x {height} m' for width, height in orientations)
            reason = (f"#{order[rid]} by sunlight ({props['annual_yield_kwh_per_kw']:,.0f} kWh/kW/year): "
                      f"{len(panels)} panels fit at {sizes} with 0.2 m gaps, limited by area, budget and target.")
        elif not props.get('annual_yield_kwh_per_kw') or not props.get('flux_coverage_percent'):
            reason = 'No usable sunlight estimate; this roof cannot contribute to the panel plan.'
        else:
            reason = 'No panels here: sunnier roofs met the target or used the budget, or no module fits.'
        reasons.append({'roof_id': rid, 'panel_count': len(panels), 'excluded': rid in excluded, 'reason': reason})
    return reasons


def apply_selection(result, plan, known_roofs, excluded_roof_ids):
    result['roof_selection'] = {'excluded_roof_ids': excluded_roof_ids,
                                'included_roof_ids': sorted(known_roofs - set(excluded_roof_ids))}
    plan['placement_explanation'] = (
        'The planner considers only included roofs with usable sunlight data, sunniest roofs first. '
        'On each roof it aligns a grid with the building, tests both module orientations, keeps rectangles fully inside the outline '
        'with 0.2 m gaps, and fills the sunniest spots first using Google\'s per-pixel sunlight. It stops at the energy target, '
        'budget, module-area cap or available space. Rooftop equipment, slope and required setbacks are not detected: '
        'draw no-panel zones over them. This is a screening layout, not an installation design.')
    plan['roof_placement_reasons'] = placement_reasons(result, plan)
    result['proposal'] = plan
    result['impact'] = estimate_impact(plan, result.get('request', {}).get('state'))
