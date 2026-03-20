from openai import OpenAI
from django.conf import settings
import mimetypes
import os

import pdfplumber

client = OpenAI(
	base_url="https://models.inference.ai.azure.com",
	api_key=settings.GITHUB_API_KEY)


def _extract_pdf_text(file_path):
    pages = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                pages.append(page_text)
    return "\n\n".join(pages).strip()


def _extract_text_content(file_path):
    filename = os.path.basename(file_path)
    mime_type, _ = mimetypes.guess_type(filename)
    extension = os.path.splitext(filename)[1].lower()

    if mime_type == "application/pdf" or extension == ".pdf":
        return _extract_pdf_text(file_path)

    if extension != ".txt":
        return ""

    with open(file_path, "rb") as file_obj:
        raw_bytes = file_obj.read()

    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            text = raw_bytes.decode(encoding)
            return text.strip()
        except UnicodeDecodeError:
            continue

    return raw_bytes.decode("utf-8", errors="ignore").strip()


def extract_and_summarize(file_path):
    filename = os.path.basename(file_path)
    extracted_text = _extract_text_content(file_path)

    if not extracted_text:
        return ""

    response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": f"""
Extract important medical details from the following document text.
File name: {filename}

Return summary including:
- Diagnosis
- Medications
- Lab abnormalities
- Important notes
 
Document text:
{extracted_text}
"""
                }
            ]
        )

    return response.choices[0].message.content or ""
  
