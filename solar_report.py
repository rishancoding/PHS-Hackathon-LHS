"""Self-contained multi-page PDF reports using the existing Pillow dependency."""
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from solar_address import InputError


def number(value):
    return f"{value:,.0f}" if value is not None else "unavailable"


def report_models(result, model_ids):
    models = result.get('panel_recommendations', {}).get('recommendations', [])
    known = {item['model']['model_id']: item for item in models}
    if len(model_ids) > 2 or len(set(model_ids)) != len(model_ids) or any(mid not in known for mid in model_ids):
        raise InputError('Choose up to two different models from this analysis for the report comparison.')
    return [known[mid] for mid in model_ids]


@lru_cache(maxsize=16)
def font(size, bold=False):
    candidates = ['/System/Library/Fonts/Supplemental/Arial Bold.ttf' if bold else
                  '/System/Library/Fonts/Supplemental/Arial.ttf',
                  '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else
                  '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size=size)


def annotated_map(result, directory):
    path = Path(directory) / 'aerial.png'
    if not path.is_file():
        return None
    with Image.open(path) as source:
        picture = source.convert('RGBA')
    # Render overlays at report resolution so sub-pixel modules remain visible.
    scale = 1200 / max(picture.size)
    picture = picture.resize((max(1, round(picture.width * scale)), max(1, round(picture.height * scale))),
                             Image.Resampling.BILINEAR)
    overlay = Image.new('RGBA', picture.size)
    draw = ImageDraw.Draw(overlay)
    sx = picture.width / result['analysis']['raster_width']
    sy = picture.height / result['analysis']['raster_height']
    excluded = set(result.get('roof_selection', {}).get('excluded_roof_ids', []))

    def outline(geometry, fill, stroke):
        polygons = [geometry['coordinates']] if geometry['type'] == 'Polygon' else geometry['coordinates']
        for polygon in polygons:
            points = [(p[0] * sx, p[1] * sy) for p in polygon[0]]
            draw.polygon(points, fill=fill)
            draw.line(points, fill=stroke, width=max(1, round(picture.width / 650)))
            # Preserve holes in detected roofs.
            for ring in polygon[1:]:
                draw.polygon([(p[0] * sx, p[1] * sy) for p in ring], fill=(0, 0, 0, 0))
    for roof in result['roofs']:
        if roof.get('preview_geometry'):
            off = roof['properties']['id'] in excluded
            outline(roof['preview_geometry'], (130, 140, 150, 30) if off else (35, 165, 90, 30),
                    (170, 180, 190, 230) if off else (90, 240, 130, 255))
    for feature in result.get('existing_panels', []):
        outline(feature['preview_geometry'], (255, 138, 61, 110), (255, 179, 122, 255))
    for panel in result.get('proposal', {}).get('panels', []):
        outline(panel['preview_geometry'], (41, 140, 255, 240), (215, 235, 255, 255))
    combined = Image.alpha_composite(picture, overlay)
    background = Image.new('RGBA', picture.size, '#e2e8eb')
    return Image.alpha_composite(background, combined).convert('RGB')


class PDFPages:
    width, height, margin = 1240, 1754, 75

    def __init__(self):
        self.pages = []
        self.new_page()

    def new_page(self):
        self.image = Image.new('RGB', (self.width, self.height), 'white')
        self.draw = ImageDraw.Draw(self.image)
        self.pages.append(self.image)
        self.draw.rectangle((0, 0, self.width, 12), fill='#246b60')
        self.draw.text((self.margin, 30), 'RoofReady  /  Solar planning report', font=font(23, True), fill='#315e82')
        self.y = 100

    def text(self, text, size=25, bold=False, color='#203542'):
        face = font(size, bold)
        lines = []
        # Wrap by pixel width, including long source URLs without discarding characters.
        maximum_width = self.width - self.margin * 2
        for paragraph in str(text).split('\n'):
            line = ''
            for word in paragraph.split():
                candidate = (line + ' ' + word) if line else word
                if self.draw.textlength(candidate, font=face) <= maximum_width:
                    line = candidate
                    continue
                if line:
                    lines.append(line)
                line = ''
                if self.draw.textlength(word, font=face) <= maximum_width:
                    line = word
                    continue
                # Only long uninterrupted values such as source URLs need character wrapping.
                for character in word:
                    if self.draw.textlength(line + character, font=face) > maximum_width:
                        lines.append(line); line = character
                    else:
                        line += character
            lines.append(line)
        for line in lines:
            if self.y + size * 1.5 > self.height - 95:
                self.new_page()
            self.draw.text((self.margin, self.y), line, font=face, fill=color)
            self.y += int(size * 1.5)
        self.y += 12

    def heading(self, text):
        if self.y > self.height - 190:
            self.new_page()
        self.y += 12
        self.text(text, size=30, bold=True, color='#246b60')

    def picture(self, picture):
        if picture is None:
            self.text('Aerial preview unavailable for this saved analysis.')
            return
        picture.thumbnail((self.width - self.margin * 2, 630))
        if self.y + picture.height + 20 > self.height - 95:
            self.new_page()
        self.image.paste(picture, (self.margin + (self.width - self.margin * 2 - picture.width) // 2, self.y))
        self.y += picture.height + 20

    def finish(self):
        for i, page in enumerate(self.pages, 1):
            draw = ImageDraw.Draw(page)
            draw.text((self.margin, self.height - 62),
                      f'Planning estimates only  |  Verify with an installer  |  Page {i} of {len(self.pages)}',
                      font=font(20), fill='#596873')
        output = BytesIO()
        self.pages[0].save(output, format='PDF', save_all=True, append_images=self.pages[1:], resolution=150,
                           title='RoofReady Solar Planning Report', author='RoofReady')
        output.seek(0)
        return output


def create_pdf_report(result, directory, model_ids=()):
    comparison = report_models(result, model_ids)
    pdf = PDFPages()
    request = result['request']; plan = result.get('proposal') or {}
    model = plan.get('panel_model') or {}
    pdf.text(request['address'], size=37, bold=True)
    pdf.text(('SYNTHETIC DEMO - invented roofs and sunlight data.\n' if result.get('demo') else '') +
             f"Radius: {request['radius_meters']:,.0f} m | Coordinates: {request['latitude']:.5f}, {request['longitude']:.5f}")
    pdf.text(f"Selected panel: {model.get('manufacturer', '')} {model.get('model_name', 'No catalog model selected')}", bold=True)
    pdf.picture(annotated_map(result, directory))
    pdf.text('Green: included roofs | Gray: excluded roofs | Blue: proposed modules | Orange: possible existing panels', size=22)
    date = result.get('imagery', {}).get('imageryDate')
    pdf.text('Imagery: ' + ('synthetic sample' if result.get('demo') else f'Google Solar API; date: {date or "unavailable"}'), size=22)
    for key, heading in [('findings', 'Findings'), ('next_steps', 'Next steps')]:
        pdf.heading(heading)
        pdf.text(result.get('report', {}).get(key, 'Not available for this saved analysis.'))
    if plan:
        pdf.heading('Selected layout and cost')
        pdf.text(f"{plan['panel_count']} panels | {plan['panel_width_m']} x {plan['panel_height_m']} m per module | {plan['panel_kw'] * 1000:g} W/module\n"
                 f"Estimated output: {plan['estimated_energy_kwh_year']:,.0f} kWh/year\n"
                 f"Installed estimate: ${plan['estimated_cost_usd']:,.2f} | Budget: ${plan['budget_usd']:,.2f}\n"
                 f"Annual target: {plan['energy_target_kwh_year']:,.0f} kWh | Shortfall: {plan['energy_shortfall_kwh_year']:,.0f} kWh\n"
                 f"Module area used: {plan.get('panel_area_used_m2', 0):,.1f} m² | Area cap: {plan.get('space_limit_m2') or 'not set'}\n"
                 f"Status: {plan['status']}")
        scope = 'panels + other equipment + installation' if plan.get('budget_includes_installation', True) else 'module hardware only; other equipment and installation cost extra'
        pdf.text(f"Budget scope: {scope}. Cost charged to budget: ${plan.get('budget_cost_usd', plan['estimated_cost_usd']):,.2f}.")
        cost_range = plan.get('estimated_cost_range_usd')
        if cost_range:
            pdf.text(f'Estimated cost range: ${cost_range[0]:,.2f}–${cost_range[1]:,.2f}')
        if 'estimated_panel_hardware_cost_usd' in plan:
            pdf.text(f"Modules: ${plan['estimated_panel_hardware_cost_usd']:,.2f}; other equipment + installation: "
                     f"${plan['estimated_other_equipment_installation_cost_usd']:,.2f}")
        if model:
            pdf.text(f"Listed module price: ${model.get('price_usd', 0):,.2f}; checked {model.get('price_date') or 'date unavailable'}.\n"
                     f"Product warranty: {model.get('product_warranty_years') or 'unknown'} years; "
                     f"performance warranty: {model.get('performance_warranty_years') or 'unknown'} years.", size=23)
        for label, field in [('Panel specifications', 'specifications_source_url'), ('Panel price source', 'price_source_url')]:
            if model.get(field): pdf.text(f'{label}: {model[field]}', size=22)
        pdf.heading('Why these panel locations?')
        pdf.text(plan.get('placement_explanation', 'This older saved layout uses roof-average yield and geometric fit. Replan to generate per-roof reasons.'))
        for reason in plan.get('roof_placement_reasons', []):
            roof = next(r for r in result['roofs'] if r['properties']['id'] == reason['roof_id'])
            pdf.text(f"{roof['properties'].get('address', reason['roof_id'])} ({reason['roof_id']}): {reason['reason']}", size=23)
    if comparison:
        pdf.heading('Your panel comparison')
        pdf.text('Both alternatives use the same roof selection, budget scope, annual target and area cap. Each layout uses one model throughout.')
        for item in comparison:
            m = item['model']
            pdf.text(f"{m['manufacturer']} {m['model_name']}", bold=True)
            pdf.text(f"Tier {item['tier']} | {item['panel_count']} panels | {m['rated_power_w']} W/module | {m['width_m']} x {m['height_m']} m\n"
                     f"Installed: ${item['estimated_cost_usd']:,.2f} | Output: {item['estimated_energy_kwh_year']:,.0f} kWh/year\n"
                     f"Area: {item['panel_area_used_m2']:,.1f} m² | Shortfall: {item['energy_shortfall_kwh_year']:,.0f} kWh/year\n"
                     f"Product warranty: {m.get('product_warranty_years') or 'unknown'} years | "
                     f"Performance warranty: {m.get('performance_warranty_years') or 'unknown'} years")
            pdf.text(' '.join(item['reasons']), size=23)
    pdf.heading('Detected roof candidates')
    excluded = set(result.get('roof_selection', {}).get('excluded_roof_ids', []))
    for roof in result['roofs']:
        p = roof['properties']
        pdf.text(f"{p.get('rank', '—')}. {p.get('address', 'Address unavailable')} ({p['id']})", bold=True, size=25)
        pdf.text(f"{'Excluded from layout' if p['id'] in excluded else 'Included for planning'} | Address status: {p.get('address_status', 'unavailable')}\n"
                 f"Footprint: {p['area_m2']:,.1f} m² | Average yield: {number(p.get('annual_yield_kwh_per_kw'))} kWh/kW/year\n"
                 f"Potential output: {number(p.get('estimated_energy_kwh_year'))} kWh/year | Sunlight coverage: {p['flux_coverage_percent']:,.1f}%\n"
                 f"Partial roof at boundary: {'yes' if p.get('partial_roof') else 'no'}", size=23)
    pdf.heading('Assumptions and limitations')
    pdf.text(f"Analysis resolution: {result['analysis']['resolution_m']} m/pixel. Roof assumptions: {result['assumptions']}", size=23)
    pdf.text(plan.get('notes', 'Screening estimates; not an installation design or quote.'), size=23)
    pdf.text(plan.get('installation_estimate', {}).get('notes', '') if plan.get('installation_estimate') else '', size=23)
    pdf.text(result.get('panel_recommendations', {}).get('notes', ''), size=23)
    pdf.text('Roof outlines do not establish property ownership. Nearby address matches are approximate. Warranty coverage is not proof of lifespan. Missing sunlight data is excluded; existing panels, obstructions, slope and required setbacks are not detected.', size=23)
    return pdf.finish()
