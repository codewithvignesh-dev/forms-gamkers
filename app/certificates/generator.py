from datetime import datetime, timezone
import io
from pathlib import Path
import string
import secrets

from reportlab.lib.colors import HexColor
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


PAGE_WIDTH = 842
PAGE_HEIGHT = 536
INK = HexColor("#182B3A")
MUTED = HexColor("#526473")
GOLD = HexColor("#B88A45")
PAPER = HexColor("#FBFAF7")
LINE = HexColor("#D9DFE2")


def render_description(description, name, college_name, certificate_number, date):
    replacements = {
        "{name}": name,
        "{college_name}": college_name,
        "{certificate_number}": certificate_number,
        "{date}": date,
    }
    for variable, value in replacements.items():
        description = description.replace(variable, value)
    return description


def generate_certificate_number():
    """Return a non-sequential, human-readable public certificate number."""
    year = datetime.now(timezone.utc).year
    alphabet = string.ascii_uppercase + string.digits
    random_code = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"CERT-{year}-{random_code}"


def generate_public_token():
    return secrets.token_urlsafe(32)


def fit_font_size(text, font_name, max_size, max_width, min_size=14):
    size = max_size
    while size > min_size and stringWidth(text, font_name, size) > max_width:
        size -= 1
    return size


def wrap_text(text, font_name, font_size, max_width):
    lines = []
    for paragraph in text.splitlines() or [""]:
        current_line = ""
        for word in paragraph.split():
            if stringWidth(word, font_name, font_size) > max_width:
                if current_line:
                    lines.append(current_line)
                    current_line = ""
                chunk = ""
                for character in word:
                    if chunk and stringWidth(
                        chunk + character, font_name, font_size
                    ) > max_width:
                        lines.append(chunk)
                        chunk = character
                    else:
                        chunk += character
                current_line = chunk
                continue
            test_line = f"{current_line} {word}".strip()
            if stringWidth(test_line, font_name, font_size) <= max_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word
        if current_line:
            lines.append(current_line)
    return lines


def generate_pdf(
    title,
    description,
    name,
    college_name,
    certificate_number,
    output_path,
    issue_date=None,
):
    if hasattr(output_path, "write"):
        canvas_target = output_path
    else:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas_target = str(output_path)
    pdf = canvas.Canvas(canvas_target, pagesize=(PAGE_WIDTH, PAGE_HEIGHT))
    center_x = PAGE_WIDTH / 2
    content_width = PAGE_WIDTH - 156
    issue_date = issue_date or datetime.now(timezone.utc).strftime("%d %B %Y")

    pdf.setFillColor(PAPER)
    pdf.rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT, fill=1, stroke=0)
    pdf.setFillColor(INK)
    pdf.rect(0, PAGE_HEIGHT - 72, PAGE_WIDTH, 72, fill=1, stroke=0)
    pdf.setStrokeColor(GOLD)
    pdf.setLineWidth(2)
    pdf.line(0, PAGE_HEIGHT - 73, PAGE_WIDTH, PAGE_HEIGHT - 73)

    pdf.setFillColor(GOLD)
    emblem = pdf.beginPath()
    emblem.moveTo(48, 493)
    emblem.lineTo(59, 504)
    emblem.lineTo(70, 493)
    emblem.lineTo(59, 482)
    emblem.close()
    pdf.drawPath(emblem, fill=1, stroke=0)
    pdf.setFillColor(PAPER)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(82, 493, "GAMKERS PORTAL")
    pdf.setFillColor(HexColor("#D7E0E5"))
    pdf.setFont("Helvetica", 8)
    pdf.drawRightString(PAGE_WIDTH - 48, 493, "OFFICIAL CERTIFICATE")

    pdf.setStrokeColor(LINE)
    pdf.setLineWidth(0.7)
    pdf.rect(32, 30, PAGE_WIDTH - 64, PAGE_HEIGHT - 120, fill=0, stroke=1)
    pdf.setFillColor(GOLD)
    pdf.rect(32, 30, 4, PAGE_HEIGHT - 120, fill=1, stroke=0)

    pdf.setFillColor(GOLD)
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawCentredString(center_x, 426, "CERTIFICATE OF ACHIEVEMENT")

    title_size = fit_font_size(title, "Helvetica-Bold", 17, content_width, min_size=10)
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", title_size)
    pdf.drawCentredString(center_x, 398, title)

    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 9)
    pdf.drawCentredString(center_x, 354, "THIS CERTIFICATE IS PROUDLY PRESENTED TO")

    name_size = fit_font_size(name, "Times-Bold", 37, content_width, min_size=15)
    pdf.setFillColor(INK)
    pdf.setFont("Times-Bold", name_size)
    pdf.drawCentredString(center_x, 310, name)
    name_width = min(stringWidth(name, "Times-Bold", name_size) + 60, content_width)
    pdf.setStrokeColor(GOLD)
    pdf.setLineWidth(1)
    pdf.line(
        center_x - name_width / 2,
        298,
        center_x + name_width / 2,
        298,
    )

    college_size = fit_font_size(
        college_name, "Helvetica-Bold", 11, content_width, min_size=8
    )
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", college_size)
    pdf.drawCentredString(center_x, 276, college_name)

    rendered_description = render_description(
        description, name, college_name, certificate_number, issue_date
    )
    description_size = 10
    lines = wrap_text(
        rendered_description, "Helvetica", description_size, content_width
    )
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", description_size)
    description_y = 244
    for line in lines[:3]:
        pdf.drawCentredString(center_x, description_y, line)
        description_y -= 15

    pdf.setStrokeColor(LINE)
    pdf.line(78, 93, PAGE_WIDTH - 78, 93)
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica-Bold", 7.5)
    pdf.drawString(84, 72, "CERTIFICATE NUMBER")
    pdf.drawRightString(PAGE_WIDTH - 84, 72, "DATE ISSUED")
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(84, 55, certificate_number)
    pdf.drawRightString(PAGE_WIDTH - 84, 55, issue_date)
    pdf.save()


def generate_pdf_bytes(
    title,
    description,
    name,
    college_name,
    certificate_number,
    issue_date=None,
):
    buffer = io.BytesIO()
    generate_pdf(
        title=title,
        description=description,
        name=name,
        college_name=college_name,
        certificate_number=certificate_number,
        output_path=buffer,
        issue_date=issue_date,
    )
    buffer.seek(0)
    return buffer
