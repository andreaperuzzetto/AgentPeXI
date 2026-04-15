"""SVGGenerator — genera SVG bundle per Cricut/Silhouette (cut files + quote SVG)."""

from __future__ import annotations

import asyncio
import logging
import math
from pathlib import Path

import svgwrite

logger = logging.getLogger("agentpexi.svg_gen")

# Dimensioni standard SVG per Cricut (pollici → px a 96dpi)
SVG_WIDTH = "12in"
SVG_HEIGHT = "12in"
SVG_VIEWBOX = "0 0 1152 1152"  # 12 * 96

# Tipi supportati
SVG_TYPES = ("mandala", "geometric", "quote", "floral_frame")


class SVGGenerator:
    """
    Genera SVG bundle per Cricut/Silhouette.

    Tipi supportati:
    - mandala: pattern mandala geometrico con N livelli di simmetria
    - geometric: forme geometriche ripetute (triangoli, esagoni, diamanti)
    - quote: testo decorativo con cornice ornamentale
    - floral_frame: cornice floreale stilizzata (foglie e fiori semplici)

    Genera bundle da 5 file SVG — 5 varianti colore della stessa composizione.
    Ogni SVG include anche un layer per anteprima PNG (Design Agent usa Playwright).
    """

    # ------------------------------------------------------------------
    # Entry point principale — chiamato da Design Agent
    # ------------------------------------------------------------------

    async def generate_bundle(
        self,
        brief: dict,
        output_dir: Path,
    ) -> list[Path]:
        """
        Genera un bundle di 5 SVG file dal brief Research.

        Args:
            brief: dict con niche, svg_type, colors (lista di 5 palette),
                   quote (opzionale per svg_type=quote), complexity (1-3)
            output_dir: directory dove salvare i file

        Returns:
            Lista di Path ai file SVG generati (5 file)
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        svg_type = brief.get("svg_type", "geometric")
        if svg_type not in SVG_TYPES:
            logger.warning("SVG type '%s' non supportato, usando 'geometric'", svg_type)
            svg_type = "geometric"

        niche_slug = brief.get("niche", "design").lower().replace(" ", "_")[:20]

        # 5 palette colore — una per variante
        color_variants = brief.get("color_variants", _default_color_variants())
        if len(color_variants) < 5:
            color_variants = (color_variants * 5)[:5]

        generators = {
            "mandala": self._generate_mandala,
            "geometric": self._generate_geometric,
            "quote": self._generate_quote,
            "floral_frame": self._generate_floral_frame,
        }
        gen_fn = generators[svg_type]

        tasks = []
        paths = []
        for i, palette in enumerate(color_variants[:5], start=1):
            output_path = output_dir / f"{niche_slug}_{svg_type}_v{i}.svg"
            paths.append(output_path)
            tasks.append(gen_fn(brief=brief, palette=palette, output_path=output_path))

        await asyncio.gather(*tasks)

        logger.info(
            "SVG bundle generato: %d file in %s (type: %s)",
            len(paths), output_dir, svg_type,
        )
        return paths

    # ------------------------------------------------------------------
    # Mandala
    # ------------------------------------------------------------------

    async def _generate_mandala(
        self,
        brief: dict,
        palette: dict,
        output_path: Path,
    ) -> Path:
        """
        Genera un mandala con simmetria radiale a 8 o 12 raggi.
        Complexity 1=semplice, 2=dettagliato, 3=intricato.
        """
        complexity = brief.get("complexity", 2)
        symmetry = {1: 8, 2: 12, 3: 16}.get(complexity, 12)

        dwg = svgwrite.Drawing(
            str(output_path),
            size=(SVG_WIDTH, SVG_HEIGHT),
            viewBox=SVG_VIEWBOX,
        )
        dwg.add(dwg.rect(insert=(0, 0), size=("100%", "100%"), fill=palette["bg"]))

        cx, cy = 576, 576  # centro
        layers = {1: 3, 2: 5, 3: 7}.get(complexity, 5)

        colors_cycle = [palette["primary"], palette["accent"], palette["secondary"]]

        for layer_idx in range(layers):
            radius = 80 + layer_idx * 80
            n_elements = symmetry
            color = colors_cycle[layer_idx % len(colors_cycle)]

            for i in range(n_elements):
                angle = (2 * math.pi / n_elements) * i
                x = cx + radius * math.cos(angle)
                y = cy + radius * math.sin(angle)

                if layer_idx % 3 == 0:
                    # Petalo ellittico
                    el = dwg.ellipse(
                        center=(x, y),
                        r=(22 - layer_idx * 2, 10),
                        fill=color,
                        fill_opacity=0.75,
                        transform=f"rotate({math.degrees(angle)}, {x}, {y})",
                    )
                elif layer_idx % 3 == 1:
                    # Diamante (rotated square)
                    size = 18 - layer_idx
                    points = [
                        (x, y - size),
                        (x + size, y),
                        (x, y + size),
                        (x - size, y),
                    ]
                    el = dwg.polygon(points=points, fill=color, fill_opacity=0.80)
                else:
                    # Cerchio
                    el = dwg.circle(
                        center=(x, y),
                        r=12 - layer_idx,
                        fill=color,
                        fill_opacity=0.70,
                    )
                dwg.add(el)

        # Centro
        dwg.add(dwg.circle(center=(cx, cy), r=30, fill=palette["primary"]))
        dwg.add(dwg.circle(center=(cx, cy), r=18, fill=palette["bg"]))
        dwg.add(dwg.circle(center=(cx, cy), r=8, fill=palette["accent"]))

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, dwg.save)
        return output_path

    # ------------------------------------------------------------------
    # Geometric
    # ------------------------------------------------------------------

    async def _generate_geometric(
        self,
        brief: dict,
        palette: dict,
        output_path: Path,
    ) -> Path:
        """
        Genera pattern geometrico con triangoli e linee.
        Adatto per cut files Cricut — forme semplici e nette.
        """
        complexity = brief.get("complexity", 2)
        grid_size = {1: 4, 2: 6, 3: 8}.get(complexity, 6)

        dwg = svgwrite.Drawing(
            str(output_path),
            size=(SVG_WIDTH, SVG_HEIGHT),
            viewBox=SVG_VIEWBOX,
        )
        dwg.add(dwg.rect(insert=(0, 0), size=("100%", "100%"), fill=palette["bg"]))

        cell = 1152 // grid_size
        colors_cycle = [palette["primary"], palette["accent"], palette["secondary"]]

        for row in range(grid_size):
            for col in range(grid_size):
                x = col * cell
                y = row * cell
                color = colors_cycle[(row + col) % len(colors_cycle)]

                if (row + col) % 2 == 0:
                    # Triangolo superiore-sinistro
                    points = [(x, y), (x + cell, y), (x, y + cell)]
                else:
                    # Triangolo inferiore-destro
                    points = [
                        (x + cell, y),
                        (x + cell, y + cell),
                        (x, y + cell),
                    ]

                dwg.add(dwg.polygon(
                    points=points,
                    fill=color,
                    fill_opacity=0.85,
                    stroke=palette["bg"],
                    stroke_width=2,
                ))

        # Bordo decorativo
        margin = 40
        dwg.add(dwg.rect(
            insert=(margin, margin),
            size=(1152 - margin * 2, 1152 - margin * 2),
            fill="none",
            stroke=palette["primary"],
            stroke_width=6,
        ))

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, dwg.save)
        return output_path

    # ------------------------------------------------------------------
    # Quote SVG
    # ------------------------------------------------------------------

    async def _generate_quote(
        self,
        brief: dict,
        palette: dict,
        output_path: Path,
    ) -> Path:
        """
        Genera SVG con quote testo + cornice decorativa.
        Ottimo per cut file Cricut (taglia il testo + la cornice).
        """
        quote = brief.get("quote", brief.get("niche", "Dream Big"))
        # Tronca se troppo lungo
        if len(quote) > 60:
            quote = quote[:57] + "..."

        dwg = svgwrite.Drawing(
            str(output_path),
            size=(SVG_WIDTH, SVG_HEIGHT),
            viewBox=SVG_VIEWBOX,
        )
        dwg.add(dwg.rect(insert=(0, 0), size=("100%", "100%"), fill=palette["bg"]))

        # Cornice esterna
        margin = 80
        dwg.add(dwg.rect(
            insert=(margin, margin),
            size=(1152 - margin * 2, 1152 - margin * 2),
            fill="none",
            stroke=palette["primary"],
            stroke_width=8,
            rx=20, ry=20,
        ))
        # Cornice interna
        inner_m = 110
        dwg.add(dwg.rect(
            insert=(inner_m, inner_m),
            size=(1152 - inner_m * 2, 1152 - inner_m * 2),
            fill="none",
            stroke=palette["accent"],
            stroke_width=3,
            rx=10, ry=10,
        ))

        # Separatori decorativi
        line_y_top = 350
        line_y_bot = 800
        for y_pos in (line_y_top, line_y_bot):
            dwg.add(dwg.line(
                start=(200, y_pos), end=(952, y_pos),
                stroke=palette["accent"], stroke_width=2,
            ))

        # Diamanti decorativi sui separatori
        for x_pos in (200, 576, 952):
            for y_pos in (line_y_top, line_y_bot):
                size = 12
                points = [
                    (x_pos, y_pos - size),
                    (x_pos + size, y_pos),
                    (x_pos, y_pos + size),
                    (x_pos - size, y_pos),
                ]
                dwg.add(dwg.polygon(
                    points=points,
                    fill=palette["accent"],
                ))

        # Testo quote — split su più righe se lungo
        words = quote.split()
        lines: list[str] = []
        current = ""
        for word in words:
            test = (current + " " + word).strip()
            if len(test) > 20:
                if current:
                    lines.append(current)
                current = word
            else:
                current = test
        if current:
            lines.append(current)

        total_lines = len(lines)
        line_height = 100
        start_y = 576 - (total_lines * line_height) // 2

        for i, line in enumerate(lines):
            dwg.add(dwg.text(
                line.upper(),
                insert=(576, start_y + i * line_height),
                text_anchor="middle",
                dominant_baseline="middle",
                font_family="serif",
                font_size=min(120, 1800 // max(len(line), 1)),
                font_weight="bold",
                fill=palette["primary"],
                letter_spacing="8",
            ))

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, dwg.save)
        return output_path

    # ------------------------------------------------------------------
    # Floral Frame
    # ------------------------------------------------------------------

    async def _generate_floral_frame(
        self,
        brief: dict,
        palette: dict,
        output_path: Path,
    ) -> Path:
        """
        Genera una cornice floreale stilizzata con foglie e fiori semplici.
        Adatta per cut file Cricut e per decorare quote prints.
        """
        dwg = svgwrite.Drawing(
            str(output_path),
            size=(SVG_WIDTH, SVG_HEIGHT),
            viewBox=SVG_VIEWBOX,
        )
        dwg.add(dwg.rect(insert=(0, 0), size=("100%", "100%"), fill=palette["bg"]))

        # Cornice centrale
        margin = 150
        dwg.add(dwg.rect(
            insert=(margin, margin),
            size=(1152 - margin * 2, 1152 - margin * 2),
            fill="none",
            stroke=palette["primary"],
            stroke_width=4,
        ))

        # Angoli decorativi — gruppo di cerchi concentrici
        corners = [(margin, margin), (1152 - margin, margin),
                   (margin, 1152 - margin), (1152 - margin, 1152 - margin)]
        for cx, cy in corners:
            for r, opacity in [(40, 0.3), (25, 0.5), (14, 0.8), (6, 1.0)]:
                dwg.add(dwg.circle(
                    center=(cx, cy), r=r,
                    fill=palette["accent"] if r > 10 else palette["primary"],
                    fill_opacity=opacity,
                ))

        # Foglie sui lati (ellissi ruotate)
        leaf_positions = []
        n_leaves = 8
        # Lato superiore e inferiore
        for i in range(1, n_leaves):
            x = (1152 // n_leaves) * i
            leaf_positions.extend([
                (x, margin, 0),    # top
                (x, 1152 - margin, 180),  # bottom
            ])
        # Lati sinistro e destro
        for i in range(1, n_leaves):
            y = (1152 // n_leaves) * i
            leaf_positions.extend([
                (margin, y, 270),   # left
                (1152 - margin, y, 90),  # right
            ])

        for lx, ly, angle in leaf_positions:
            dwg.add(dwg.ellipse(
                center=(lx, ly),
                r=(20, 8),
                fill=palette["secondary"],
                fill_opacity=0.7,
                transform=f"rotate({angle}, {lx}, {ly})",
            ))

        # Fiori agli angoli interni
        flower_corners = [
            (margin + 80, margin + 80),
            (1152 - margin - 80, margin + 80),
            (margin + 80, 1152 - margin - 80),
            (1152 - margin - 80, 1152 - margin - 80),
        ]
        for fx, fy in flower_corners:
            # Petali
            for petal_angle in range(0, 360, 45):
                rad = math.radians(petal_angle)
                px = fx + 25 * math.cos(rad)
                py = fy + 25 * math.sin(rad)
                dwg.add(dwg.ellipse(
                    center=(px, py),
                    r=(14, 7),
                    fill=palette["accent"],
                    fill_opacity=0.8,
                    transform=f"rotate({petal_angle}, {px}, {py})",
                ))
            # Centro fiore
            dwg.add(dwg.circle(
                center=(fx, fy), r=12,
                fill=palette["primary"],
            ))

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, dwg.save)
        return output_path


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _default_color_variants() -> list[dict]:
    """5 palette colore predefinite quando il brief non ne specifica."""
    return [
        {"bg": "#FFFFFF", "primary": "#2C2C2C", "secondary": "#E8E8E8", "accent": "#8B7355"},
        {"bg": "#F5F0E8", "primary": "#4A3728", "secondary": "#D4C4B0", "accent": "#8B6548"},
        {"bg": "#EEF2F0", "primary": "#2D4A3E", "secondary": "#B8CFC8", "accent": "#5A8A78"},
        {"bg": "#F8F0F5", "primary": "#4A2C3D", "secondary": "#D4B8C8", "accent": "#8B5A72"},
        {"bg": "#F0F4F8", "primary": "#2C3D4A", "secondary": "#B8C8D4", "accent": "#5A728B"},
    ]
