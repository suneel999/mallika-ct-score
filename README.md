# CT Coronary Calcium Score Report Generator

Fills the existing Mallika Hospitals Word template and prints it to a one-page A4 PDF. The original template is not redesigned.

## Run

```bash
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000

## Notes

- Source template: `templates/original_template.docx` (copy of `template.docx`)
- The original `template.docx` is never overwritten
- Microsoft Word is used for PDF print export so layout stays identical
- Generated files are stored in `generated/`
- PDF name format: `CT_Calcium_Score_<patient_id>_<patient_name>.pdf`
