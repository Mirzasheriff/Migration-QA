from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

SOURCE = Path("powerbi_migrated.png")
OUTPUT = Path("powerbi_defective.png")

img = Image.open(SOURCE).convert("RGB")
draw = ImageDraw.Draw(img)

# Try to use a normal Windows font
font_path = "C:/Windows/Fonts/arial.ttf"
bold_font_path = "C:/Windows/Fonts/arialbd.ttf"

font = ImageFont.truetype(font_path, 24)
bold_font = ImageFont.truetype(bold_font_path, 32)

# --------------------------------------------------
# DEFECT 1: Change the report title
# --------------------------------------------------

# Cover the original title area
draw.rectangle((40, 20, 500, 70), fill="white")

draw.text(
    (40, 25),
    "Sales Performance",
    fill="black",
    font=bold_font
)

# --------------------------------------------------
# DEFECT 2: Change Total Sales value
# --------------------------------------------------

# Cover the original Sales KPI value
draw.rectangle((70, 120, 300, 190), fill="white")

draw.text(
    (75, 135),
    "$2.51M",
    fill="black",
    font=bold_font
)

# --------------------------------------------------
# DEFECT 3: Remove the Pie Chart
# --------------------------------------------------

# IMPORTANT:
# This rectangle intentionally covers the approximate
# Pie Chart area.

draw.rectangle(
    (40, 300, 400, 600),
    fill="white"
)

draw.text(
    (120, 430),
    "Visual Missing",
    fill="red",
    font=bold_font
)

img.save(OUTPUT)

print(f"Created defective Power BI report: {OUTPUT}")