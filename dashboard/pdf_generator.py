from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from pathlib import Path
import json

def generate_pdf(output_path, query, result):
    c = canvas.Canvas(output_path, pagesize=A4)
    width, height = A4

    c.setFont("Helvetica-Bold", 18)
    c.setFillColor(colors.darkblue)
    c.drawString(50, height - 50, "AKS Diagnosis Report")

    c.setFont("Helvetica", 12)
    c.setFillColor(colors.black)
    y = height - 100
    
    c.drawString(50, y, f"Query: {query}")
    y -= 30

    c.drawString(50, y, f"Severity: {result.get('severity', 'N/A')}")
    y -= 30

    c.drawString(50, y, "Diagnosis:")
    y -= 20
    
    text = c.beginText(50, y)
    text.setFont("Helvetica", 10)

    for line in result["diagnosis"].split("\n"):
        text.textLine(line[:120])
    c.drawText(text)

    c.save()
    return output_path
