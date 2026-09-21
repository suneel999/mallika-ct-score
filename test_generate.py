"""Generate a test PDF from the original template using the required sample data."""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from report_engine import generate_report


def sample_ct_image() -> BytesIO:
    img = Image.new("L", (900, 520), 18)
    draw = ImageDraw.Draw(img)
    draw.ellipse((90, 30, 810, 490), fill=38, outline=70, width=3)
    draw.ellipse((250, 120, 430, 330), fill=52)
    draw.ellipse((480, 110, 690, 360), fill=48)
    for box in [(310, 190, 350, 225), (560, 210, 610, 250), (400, 280, 430, 305)]:
        draw.ellipse(box, fill=230)
    img = img.filter(ImageFilter.GaussianBlur(1.2))
    rgb = Image.merge("RGB", (img, img, img))
    buffer = BytesIO()
    rgb.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def main() -> None:
    fields = {
        "name": "Pandajah",
        "id": "MH120010",
        "age": 45,
        "ref": "Dr. J Mahesh Babu MD DM",
        "s_date": "2026-09-08",
        "r_date": "2026-09-08",
        "score": 0,
        "start": "0",
        "end": "10",
        "lm": 0,
        "lad": 0,
        "lcx": 0,
        "rca": 0,
        "total": 0,
        "auto_total": True,
        "reportedby": "Dr. Kanav Kansal",
        "qual": "DNB Radiodiagnosis",
        "des": "Consultant Radiologist",
        "risk": "Very Low",
    }
    out_dir = Path("generated") / "sample_test"
    out_dir.mkdir(parents=True, exist_ok=True)
    sample_path = Path("static") / "sample_ct.png"
    sample_path.parent.mkdir(parents=True, exist_ok=True)
    image = sample_ct_image()
    sample_path.write_bytes(image.getvalue())
    image.seek(0)
    result = generate_report(fields, image, "sample_test")
    print("PDF:", result["pdf_path"])
    print("filename:", result["download_name"])
    print("verification:", result["verification"])
    print("data:", result["data"])


if __name__ == "__main__":
    main()
