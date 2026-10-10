"""Compare catalog modules using the same budget-constrained layouts shown on the map."""
import csv
import math
from pathlib import Path

from installation_cost import estimate_installation_cost
from panel_plan import propose_panels, validate_plan_inputs
from solar_address import InputError

CATALOG = Path(__file__).with_name('panel_catalog (2).csv')
PRIORITIES = {'balanced', 'lowest_cost', 'space_efficiency', 'longer_warranty'}
# Explicit modeling allowance, not a measured module price or local labor quote.
REFERENCE_MODULE_ALLOWANCE_PER_WATT = .35


def load_catalog(path=CATALOG):
    try:
        with Path(path).open(newline='', encoding='utf-8-sig') as file:
            rows = list(csv.DictReader(file))
    except (OSError, csv.Error) as exc:
        raise InputError('Panel catalog could not be read. Check panel_catalog (2).csv.') from exc
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
    values.update(priority=priority, space_limit_m2=space, selected_model_id=selected)
    return values


def build_recommendations(result, budget_usd=25000, energy_target_kwh_year=10000,
                          priority='balanced', space_limit_m2=None, selected_model_id=None, catalog=None):
    inputs = validate_recommendation_inputs(dict(budget_usd=budget_usd, energy_target_kwh_year=energy_target_kwh_year,
                                               priority=priority, space_limit_m2=space_limit_m2,
                                               selected_model_id=selected_model_id))
    models = load_catalog() if catalog is None else catalog
    eligible = [m for m in models if m.get('panel_type') == 'rigid']
    excluded = [{'model_id': m['model_id'], 'model_name': m['model_name'],
                 'reason': 'Portable/foldable module: not compared as permanent roof hardware.' if m.get('panel_type') == 'portable_foldable'
                 else 'Flexible or unclassified module: mounting compatibility needs a separate model.'}
                for m in models if m not in eligible]
    if not eligible:
        raise InputError('Catalog has no rigid modules for rooftop comparison.')
    if selected_model_id and selected_model_id not in {m['model_id'] for m in eligible}:
        raise InputError('Selected panel is not an eligible rigid catalog module.')
    geographic = estimate_installation_cost(result)
    non_module_rate = max(0, geographic['installed_cost_per_watt'] - REFERENCE_MODULE_ALLOWANCE_PER_WATT)
    evaluated = []
    for model in eligible:
        unit_cost = model['price_usd'] + model['rated_power_w'] * non_module_rate
        plan = propose_panels(result, budget_usd, energy_target_kwh_year,
                              installed_cost_per_watt=unit_cost / model['rated_power_w'],
                              panel_model=model, space_limit_m2=space_limit_m2)
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
        summary = {key: plan[key] for key in ('panel_count', 'estimated_cost_usd', 'estimated_energy_kwh_year',
                   'energy_shortfall_kwh_year', 'target_met', 'panel_area_used_m2', 'available_roof_area_m2',
                   'estimated_cost_range_usd', 'estimated_panel_hardware_cost_usd',
                   'estimated_other_equipment_installation_cost_usd', 'layout_search_limited')}
        recommendations.append({**summary, 'rank': index, 'tier': tier, 'model': model, 'reasons': reasons,
                                'estimated_installed_cost_per_panel_usd': round(model['rated_power_w'] * plan['installed_cost_per_watt'], 2),
                                'recommended': model['model_id'] == best_id})
        if model['model_id'] == (selected_model_id or best_id):
            selected_plan = plan
    result['proposal'] = selected_plan
    result['panel_recommendations'] = {
        'priority': inputs['priority'], 'budget_usd': budget_usd, 'energy_target_kwh_year': energy_target_kwh_year,
        'space_limit_m2': space_limit_m2, 'recommended_model_id': best_id,
        'selected_model_id': selected_plan['panel_model']['model_id'], 'catalog_count': len(models),
        'recommendations': recommendations, 'excluded_models': excluded,
        'notes': 'Stock and order availability ignored. Tiers use the central installed-cost estimate, roof-average annual yield, '
                 'actual catalog dimensions and 0.2 m gaps. Each alternative uses one model throughout the layout. '
                 'Both axis-aligned orientations are tried; this greedy screening search is not a guaranteed global optimum. '
                 'Unknown degradation is not invented, and bifacial rear gain is not added. Roof-potential rankings retain their separate density assumption.'}
    return result
