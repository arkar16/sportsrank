"""Static game-link metadata and compact local thumbnails for spread cards."""
from hashlib import sha256
from html import escape
from io import BytesIO
from pathlib import Path
import re

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

from cfb.program_marks import _atomic_write
from excitement_grades import graded_number, letter_grade


ORIGIN = "https://www.sportsrank.top"
# Keep the preview small enough for the compact Messages link treatment.
ICON_SIZE = 120
SCORE = re.compile(r"(?P<number>100\.0|[0-9]{1,2}\.[0-9])(?P<star>\*?)"
                   r"(?: \([ABCDF][+-]?\))?(?:\s*·\s*(?P<basis>.+))?")
SCRIPT = "/cfb/assets/spread-card.js?v=7"


def bev_number(label):
    match = SCORE.fullmatch((label or "").strip())
    return match["number"] if match else None


def score_paragraph(sport, bev, aev):
    if not bev:
        return ""
    fields = []
    for metric, name, label in (("BEV", "Pregame appeal", bev), ("AEV", "Game drama", aev)):
        if label:
            match = SCORE.fullmatch(label.strip())
            if match:
                label = graded_number(sport, metric, match["number"] + match["star"])
                if match["basis"]:
                    label += " · " + match["basis"]
            fields.append(f"{name} ({metric}) <strong>{escape(label)}</strong>")
    return '<p class="game-excitement">' + " · ".join(fields) + "</p>"


def preview_icon(directory, sport, mark, bev):
    path = Path(mark)
    artwork = None
    if path.is_relative_to(f"/{sport}/assets") and ".." not in path.parts:
        source = directory / path.relative_to(f"/{sport}")
        if source.is_file():
            with Image.open(source) as image:
                if bev is None and image.width == image.height and image.width <= ICON_SIZE:
                    return mark, image.width
                artwork = image.convert("RGBA")
    if artwork is None and bev is None:
        return None

    paper, green = "#faf7ef", "#195348"
    icon = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), paper if bev else (0, 0, 0, 0))
    if artwork is not None:
        height = 80 if bev else ICON_SIZE
        artwork.thumbnail((ICON_SIZE - 12, 70 if bev else ICON_SIZE - 12), Image.Resampling.LANCZOS)
        icon.alpha_composite(artwork, ((ICON_SIZE - artwork.width) // 2, (height - artwork.height) // 2))
    if bev:
        draw = ImageDraw.Draw(icon)
        grade = letter_grade(sport, "BEV", bev)
        if artwork is None:
            draw.rectangle((0, 0, 119, 119), fill=green)
            draw.text((60, 24), "BEV", font=ImageFont.load_default(size=21), anchor="mm", fill=paper)
            draw.text((60, 61), bev, font=ImageFont.load_default(size=36), anchor="mm", fill=paper)
            draw.text((60, 98), grade, font=ImageFont.load_default(size=24), anchor="mm", fill=paper)
        else:
            draw.rectangle((0, 80, 119, 119), fill=green)
            draw.text((60, 88), "BEV", font=ImageFont.load_default(size=14), anchor="mm", fill=paper)
            draw.text((60, 107), f"{bev} ({grade})", font=ImageFont.load_default(size=20), anchor="mm",
                      fill=paper, stroke_width=1, stroke_fill=paper)
    buffer = BytesIO()
    icon.save(buffer, format="PNG")
    data = buffer.getvalue()
    name = f"bev/{sha256(data).hexdigest()}.png" if bev else path.name
    target = directory / "assets/share-icons" / name
    if not target.is_file() or target.read_bytes() != data:
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, data)
    return f"/{sport}/assets/share-icons/{name}", ICON_SIZE


def social_tags(canonical, title, description, icon, home, bev, sport):
    grade = letter_grade(sport, "BEV", bev) if bev else None
    label = f"{bev} ({grade})"
    title = escape(title + (f" · BEV {label}" if bev else ""))
    tags = [f'<meta property="og:url" content="{escape(canonical)}"><meta property="og:title" content="{title}">',
            f'<meta property="og:description" content="{escape(description)}">',
            '<meta name="twitter:card" content="summary">']
    if bev:
        tags.extend([f'<meta name="twitter:title" content="{title}">',
                     f'<meta name="sportsrank:bev" content="{bev}">',
                     f'<meta name="sportsrank:bev-grade" content="{grade}">'])
    if icon:
        path, size = icon
        url = escape(ORIGIN + path)
        alt = escape(f"{home} · Pregame appeal (BEV) {label}" if bev else f"{home} school mark")
        tags.extend([f'<link rel="apple-touch-icon" sizes="{size}x{size}" href="{url}">',
                     f'<link rel="icon" type="image/png" sizes="{size}x{size}" href="{url}">',
                     f'<meta property="og:image" content="{url}"><meta property="og:image:alt" content="{alt}">',
                     f'<meta property="og:image:width" content="{size}"><meta property="og:image:height" content="{size}">',
                     f'<meta name="twitter:image" content="{url}"><meta name="twitter:image:alt" content="{alt}">'])
    return "\n".join(tags)


def refresh_retained_previews(directory, sport):
    """Upgrade the preview of an older issued pick using its original visible BEV."""
    cache = {}
    for path in (directory / "picks").rglob("*.html"):
        markup = path.read_bytes().decode("utf-8")
        if 'class="game-excitement"' not in markup:
            continue
        head, _, body = markup.partition("</head>")
        # A dated basketball pick may be restored into an empty staging tree.
        # In that case materialize its thumbnail even when the HTML is current.
        saved = re.search(r'property="og:image" content="' + re.escape(ORIGIN)
                          + rf'/{sport}/(assets/share-icons/bev/[a-f0-9]+\.png)"', head)
        if 'name="sportsrank:bev-grade"' in head and saved and (directory / saved[1]).is_file():
            continue
        page = BeautifulSoup(markup, "html.parser")
        stored = [s.get_text(strip=True) for s in page.select(".game-excitement strong")]
        bev = bev_number(stored[0]) if stored else None
        if bev is None:
            continue
        home = page.select(".game-line tbody tr")[-1]
        mark = home.select_one("img")
        key = (mark["src"] if mark else "", bev)
        if key not in cache:
            cache[key] = preview_icon(directory, sport, *key)

        def meta(name):
            return page.find("meta", property="og:" + name)["content"]

        title = re.sub(r" · BEV [\d.]+(?: \([ABCDF][+-]?\))?$", "", meta("title"))
        tags = social_tags(meta("url"), title, meta("description"), cache[key],
                           home.select_one(".school").get_text(strip=True), bev, sport)
        start = head.index('<meta property="og:url"')
        end = head.index('<link rel="stylesheet"', start)
        head = head[:start] + tags + "\n" + head[end:]
        head = re.sub(r"/cfb/assets/spread-card\.js(?:\?v=\d+)?", SCRIPT, head)
        paragraph = score_paragraph(sport, stored[0], stored[1] if len(stored) > 1 else None)
        body = re.sub(r'<p class="game-excitement">.*?</p>', lambda _: paragraph, body, count=1, flags=re.S)
        updated = head + "</head>" + body
        if updated != markup:
            _atomic_write(path, updated.encode("utf-8"))
