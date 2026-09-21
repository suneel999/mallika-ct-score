"""Fill the original Word template and convert it to a one-page A4 PDF."""

from __future__ import annotations

import io
import re
import threading
import zipfile
from pathlib import Path
from typing import BinaryIO
from xml.sax.saxutils import escape as xml_escape

from PIL import Image
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent
TEMPLATE_CANDIDATES = (
    ROOT / "template.docx",
    ROOT / "templates" / "original_template.docx",
)
GENERATED_DIR = ROOT / "generated"


def get_template_path() -> Path:
    """Use the Word file the user last saved, not a stale copy."""
    existing = [path for path in TEMPLATE_CANDIDATES if path.exists()]
    if not existing:
        raise FileNotFoundError(
            "No Word template found. Save template.docx in the project folder."
        )
    return max(existing, key=lambda path: path.stat().st_mtime)

# Image text box measured in Word: 10.65 cm × 5.33 cm.
# 1 cm = 360000 EMU. Fill this entire box, not a padded inner area.
IMAGE_BOX_CX = 3834000
IMAGE_BOX_CY = 1918800
IMAGE_BOX_PX_W = 1258  # ~300 dpi
IMAGE_BOX_PX_H = 630
TXBX_CONTENT_RE = re.compile(r"<w:txbxContent>.*?</w:txbxContent>", re.DOTALL)

PLACEHOLDER_KEYS = (
    "name",
    "id",
    "age",
    "ref",
    "s_date",
    "r_date",
    "score",
    "start",
    "end",
    "lm",
    "lad",
    "lcx",
    "rca",
    "total",
    "reportedby",
    "qual",
    "des",
    "risk",
)

WT_RE = re.compile(r"(<w:t(?:\s[^>]*)?>)(.*?)(</w:t>)", re.DOTALL)
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
_WORD_LOCK = threading.Lock()

SUPER_SUFFIX = {"st": "ˢᵗ", "nd": "ⁿᵈ", "rd": "ʳᵈ", "th": "ᵗʰ"}


def ordinal_suffix(value: str) -> str:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return "th"
    if 10 <= (number % 100) <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")


def ordinal_plain(value: str) -> str:
    return f"{value}{ordinal_suffix(value)}"


def ordinal_super(value: str) -> str:
    return f"{value}{SUPER_SUFFIX[ordinal_suffix(value)]}"


def format_score(value) -> str:
    return f"{float(value):.2f}"


def format_date_display(iso_date: str) -> str:
    """Convert HTML date (YYYY-MM-DD) to DD/MM/YYYY."""
    text = (iso_date or "").strip()
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        year, month, day = match.groups()
        return f"{day}/{month}/{year}"
    return text


def risk_from_score(score: float) -> str:
    if score <= 0:
        return "Very Low"
    if score <= 10:
        return "Low"
    if score <= 100:
        return "Moderate"
    if score <= 400:
        return "Moderate to High"
    return "High"


def build_replacement_data(fields: dict) -> dict[str, str]:
    """Central replacement dictionary. Every {{key}} uses the same value."""
    score = float(fields["score"])
    lm = float(fields["lm"])
    lad = float(fields["lad"])
    lcx = float(fields["lcx"])
    rca = float(fields["rca"])
    if fields.get("auto_total"):
        total = lm + lad + lcx + rca
    else:
        total = float(fields["total"])

    start = str(fields.get("start", "")).strip()
    end = str(fields.get("end", "")).strip()
    risk = (fields.get("risk") or "").strip() or risk_from_score(score)

    return {
        "name": str(fields["name"]).strip(),
        "id": str(fields["id"]).strip(),
        "age": str(int(fields["age"])),
        "ref": str(fields["ref"]).strip(),
        "s_date": format_date_display(fields["s_date"]),
        "r_date": format_date_display(fields["r_date"]),
        "score": format_score(score),
        "start": start,
        "end": end,
        "lm": format_score(lm),
        "lad": format_score(lad),
        "lcx": format_score(lcx),
        "rca": format_score(rca),
        "total": format_score(total),
        "reportedby": str(fields["reportedby"]).strip(),
        "qual": str(fields.get("qual") or "").strip(),
        "des": str(fields.get("des") or "").strip(),
        "risk": risk,
    }


def _joined_wt_text(xml: str) -> str:
    return "".join(match.group(2) for match in WT_RE.finditer(xml))


def replace_all_placeholders(xml: str, data: dict[str, str]) -> str:
    """Replace {{keys}} even when Word splits them across runs, e.g. { + {risk}}."""
    matches = list(WT_RE.finditer(xml))
    if not matches:
        return xml

    texts = [match.group(2) for match in matches]
    joined = "".join(texts)
    owners: list[int] = []
    for index, text in enumerate(texts):
        owners.extend([index] * len(text))

    new_texts = [""] * len(texts)
    cursor = 0
    known = set(PLACEHOLDER_KEYS) | {"image"}

    while cursor < len(joined):
        token = PLACEHOLDER_RE.match(joined, cursor)
        if token and token.group(1) in known:
            key = token.group(1)
            first_node = owners[cursor]
            end_at = token.end()
            if key == "image":
                new_texts[first_node] += "{{image}}"
            elif key == "end":
                remainder = joined[end_at:]
                if remainder.startswith("ᵗʰ"):
                    new_texts[first_node] += ordinal_super(data.get("end", ""))
                    end_at += len("ᵗʰ")
                elif remainder.startswith("th"):
                    new_texts[first_node] += ordinal_plain(data.get("end", ""))
                    end_at += 2
                else:
                    new_texts[first_node] += xml_escape(data.get("end", ""))
            else:
                new_texts[first_node] += xml_escape(data.get(key, ""))
            cursor = end_at
            continue
        new_texts[owners[cursor]] += joined[cursor]
        cursor += 1

    pieces: list[str] = []
    last = 0
    for match, new_text in zip(matches, new_texts):
        pieces.append(xml[last:match.start()])
        open_tag = match.group(1)
        if (new_text.startswith(" ") or new_text.endswith(" ")) and "xml:space" not in open_tag:
            open_tag = open_tag.replace("<w:t", '<w:t xml:space="preserve"', 1)
        pieces.append(open_tag + new_text + match.group(3))
        last = match.end()
    pieces.append(xml[last:])
    return "".join(pieces)


def leftover_placeholders(xml: str) -> list[str]:
    return sorted(set(PLACEHOLDER_RE.findall(_joined_wt_text(xml))))


def _next_rid(rels_xml: str) -> str:
    numbers = [int(n) for n in re.findall(r'Id="rId(\d+)"', rels_xml)]
    return f"rId{(max(numbers) if numbers else 0) + 1}"


def _ensure_image_content_types(ct_xml: str, extension: str, content_type: str) -> str:
    token = f'Extension="{extension}"'
    if token in ct_xml:
        return ct_xml
    default = f'<Default Extension="{extension}" ContentType="{content_type}"/>'
    return ct_xml.replace("<Types ", f"<Types ", 1).replace(
        "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">",
        "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">" + default,
        1,
    )


def _inline_drawing(rel_id: str, cx: int, cy: int, doc_pr_id: int, name: str) -> str:
    return (
        f'<w:drawing>'
        f'<wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{cx}" cy="{cy}"/>'
        f'<wp:effectExtent l="0" t="0" r="0" b="0"/>'
        f'<wp:docPr id="{doc_pr_id}" name="{name}"/>'
        f'<wp:cNvGraphicFramePr>'
        f'<a:graphicFrameLocks xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"/>'
        f"</wp:cNvGraphicFramePr>"
        f'<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        f'<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f"<pic:nvPicPr>"
        f'<pic:cNvPr id="0" name="{name}"/>'
        f"<pic:cNvPicPr/>"
        f"</pic:nvPicPr>"
        f"<pic:blipFill>"
        f'<a:blip r:embed="{rel_id}"/>'
        f"<a:stretch><a:fillRect/></a:stretch>"
        f"</pic:blipFill>"
        f'<pic:spPr bwMode="auto">'
        f"<a:xfrm>"
        f'<a:off x="0" y="0"/>'
        f'<a:ext cx="{cx}" cy="{cy}"/>'
        f"</a:xfrm>"
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        f"<a:noFill/>"
        f"<a:ln><a:noFill/></a:ln>"
        f"</pic:spPr>"
        f"</pic:pic>"
        f"</a:graphicData>"
        f"</a:graphic>"
        f"</wp:inline>"
        f"</w:drawing>"
    )


def _prepare_image_bytes(image_file: BinaryIO) -> tuple[bytes, int, int, str]:
    image_file.seek(0)
    with Image.open(image_file) as img:
        img.load()
        if img.mode in ("RGBA", "LA", "P"):
            converted = img.convert("RGBA")
            background = Image.new("RGB", converted.size, (255, 255, 255))
            if converted.mode == "RGBA":
                background.paste(converted, mask=converted.split()[-1])
            else:
                background.paste(converted)
            work = background
        else:
            work = img.convert("RGB")
        work = work.resize((IMAGE_BOX_PX_W, IMAGE_BOX_PX_H), Image.Resampling.LANCZOS)
        width, height = work.size
        buffer = io.BytesIO()
        work.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue(), width, height, "png"


def _image_paragraph(rel_id: str, cx: int, cy: int, index: int) -> str:
    drawing = _inline_drawing(rel_id, cx, cy, 990001 + index, f"CTCalcium{index + 1}")
    return (
        "<w:txbxContent>"
        '<w:p>'
        "<w:pPr>"
        '<w:spacing w:before="0" w:after="0"/>'
        '<w:jc w:val="center"/>'
        "</w:pPr>"
        f"<w:r>{drawing}</w:r>"
        "</w:p>"
        "</w:txbxContent>"
    )


def _zero_image_box_padding(xml: str) -> str:
    """Remove the Word text-box inner margins so the picture can fill 10.65×5.33 cm."""

    def zero_body_pr(match: re.Match) -> str:
        block = match.group(0)
        block = re.sub(r'\blIns="\d+"', 'lIns="0"', block)
        block = re.sub(r'\btIns="\d+"', 'tIns="0"', block)
        block = re.sub(r'\brIns="\d+"', 'rIns="0"', block)
        block = re.sub(r'\bbIns="\d+"', 'bIns="0"', block)
        return block

    xml = re.sub(
        r'name="CTCalcium\d+"[\s\S]{0,12000}?</wps:txbx><wps:bodyPr\b[^>]*>',
        zero_body_pr,
        xml,
    )

    def zero_vml_textbox(match: re.Match) -> str:
        block = match.group(0)
        if "CTCalcium" not in block:
            return block
        open_tag = match.group(1)
        rest = match.group(2)
        if "inset=" in open_tag:
            open_tag = re.sub(r'inset="[^"]*"', 'inset="0,0,0,0"', open_tag)
        else:
            open_tag = open_tag.replace("<v:textbox", '<v:textbox inset="0,0,0,0"', 1)
        return open_tag + rest

    xml = re.sub(r"(<v:textbox\b[^>]*>)([\s\S]*?</v:textbox>)", zero_vml_textbox, xml)
    return xml


def insert_image(xml: str, rel_id: str, cx: int, cy: int) -> str:
    if "{{image}}" not in xml:
        return xml

    index = 0

    def replace_box(match: re.Match) -> str:
        nonlocal index
        block = match.group(0)
        if "{{image}}" not in block:
            return block
        replacement = _image_paragraph(rel_id, cx, cy, index)
        index += 1
        return replacement

    xml = TXBX_CONTENT_RE.sub(replace_box, xml)
    xml = xml.replace("{{image}}", "")
    return _zero_image_box_padding(xml)


def fill_template(data: dict[str, str], image_file: BinaryIO | None, dest_docx: Path) -> list[str]:
    template_path = get_template_path()

    dest_docx.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(template_path, "r") as src:
        parts = {name: src.read(name) for name in src.namelist()}

    for part_name in ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml"):
        if part_name in parts:
            parts[part_name] = replace_all_placeholders(
                parts[part_name].decode("utf-8"), data
            ).encode("utf-8")

    document_xml = parts["word/document.xml"].decode("utf-8")

    rels_xml = parts["word/_rels/document.xml.rels"].decode("utf-8")
    content_types = parts["[Content_Types].xml"].decode("utf-8")

    if image_file is not None:
        image_bytes, pixel_w, pixel_h, ext = _prepare_image_bytes(image_file)
        cx, cy = IMAGE_BOX_CX, IMAGE_BOX_CY
        rel_id = _next_rid(rels_xml)
        media_name = f"word/media/image_ct.{ext}"
        parts[media_name] = image_bytes
        rels_xml = rels_xml.replace(
            "</Relationships>",
            f'<Relationship Id="{rel_id}" '
            f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            f'Target="media/image_ct.{ext}"/></Relationships>',
        )
        content_types = _ensure_image_content_types(content_types, "png", "image/png")
        document_xml = insert_image(document_xml, rel_id, cx, cy)
    else:
        document_xml = document_xml.replace("{{image}}", "")

    leftovers = leftover_placeholders(document_xml)
    parts["word/document.xml"] = document_xml.encode("utf-8")
    parts["word/_rels/document.xml.rels"] = rels_xml.encode("utf-8")
    parts["[Content_Types].xml"] = content_types.encode("utf-8")

    with zipfile.ZipFile(dest_docx, "w", zipfile.ZIP_DEFLATED) as dest:
        for name, payload in parts.items():
            dest.writestr(name, payload)

    return leftovers


def convert_docx_to_pdf(docx_path: Path, pdf_path: Path) -> None:
    """Print the filled DOCX through Microsoft Word (installed on this machine)."""
    import pythoncom
    import win32com.client

    docx_path = Path(docx_path).resolve()
    pdf_path = Path(pdf_path).resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if pdf_path.exists():
        pdf_path.unlink()

    pythoncom.CoInitialize()
    word = None
    document = None
    try:
        with _WORD_LOCK:
            word = win32com.client.DispatchEx("Word.Application")
            word.Visible = False
            word.DisplayAlerts = 0
            document = word.Documents.Open(
                str(docx_path),
                ReadOnly=True,
                AddToRecentFiles=False,
                Visible=False,
            )
            # 17 = wdExportFormatPDF, 0 = wdExportOptimizeForPrint
            document.ExportAsFixedFormat(
                OutputFileName=str(pdf_path),
                ExportFormat=17,
                OpenAfterExport=False,
                OptimizeFor=0,
                Item=0,
                IncludeDocProps=False,
                KeepIRM=True,
                CreateBookmarks=0,
                DocStructureTags=True,
                BitmapMissingFonts=True,
                UseISO19005_1=False,
            )
            document.Close(False)
            document = None
            word.Quit()
            word = None
    finally:
        if document is not None:
            try:
                document.Close(False)
            except Exception:
                pass
        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()


def verify_pdf(pdf_path: Path) -> dict:
    reader = PdfReader(str(pdf_path))
    page_count = len(reader.pages)
    page = reader.pages[0]
    box = page.mediabox
    width_pt = float(box.width)
    height_pt = float(box.height)
    width_mm = width_pt * 25.4 / 72
    height_mm = height_pt * 25.4 / 72
    text = ""
    for pdf_page in reader.pages:
        text += pdf_page.extract_text() or ""
    leftover = sorted(set(PLACEHOLDER_RE.findall(text)))
    a4 = abs(width_mm - 210) < 3 and abs(height_mm - 297) < 3
    return {
        "page_count": page_count,
        "width_mm": round(width_mm, 2),
        "height_mm": round(height_mm, 2),
        "is_a4": a4,
        "one_page": page_count == 1,
        "leftover_placeholders": leftover,
        "ok": page_count == 1 and a4 and not leftover,
    }


def pdf_filename(patient_id: str, patient_name: str) -> str:
    def clean(part: str) -> str:
        part = re.sub(r'[\\/:*?"<>|]+', "", part or "")
        part = re.sub(r"\s+", "_", part.strip())
        return part or "patient"

    return f"CT_Calcium_Score_{clean(patient_id)}_{clean(patient_name)}.pdf"


def generate_report(
    fields: dict,
    image_file: BinaryIO | None,
    output_stem: str,
) -> dict:
    data = build_replacement_data(fields)
    work_dir = GENERATED_DIR / output_stem
    work_dir.mkdir(parents=True, exist_ok=True)
    docx_path = work_dir / "filled.docx"
    download_name = pdf_filename(data["id"], data["name"])
    pdf_path = work_dir / download_name

    leftovers = fill_template(data, image_file, docx_path)
    convert_docx_to_pdf(docx_path, pdf_path)
    verification = verify_pdf(pdf_path)
    verification["xml_leftovers"] = leftovers
    return {
        "data": data,
        "docx_path": docx_path,
        "pdf_path": pdf_path,
        "download_name": download_name,
        "verification": verification,
    }
