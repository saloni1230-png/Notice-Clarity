from pathlib import Path

import pymupdf


def main():
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(35, 35, 560, 790), color=(0.2, 0.3, 0.2))
    page.insert_text(
        (55, 70), "SYNTHETIC EXAMPLE - NOT AN OFFICIAL NOTICE", fontsize=14, color=(0.6, 0.2, 0.1)
    )
    page.insert_text(
        (55, 125),
        "Income Tax Department - EXAMPLE ONLY\n\n"
        "Reference number: TEST-2026-001\n\nDate of issue: 20 October 2026\n\n"
        "Please reply before 31 October 2026.\n\nAmount mentioned: INR 12,500.00\n\n"
        "Please provide a copy of the supporting bank statement.\n\n"
        "This is a fictional test document for the Notice Clarity workspace.\n"
        "No payment, response, or legal action is required for this example.",
        fontsize=12,
    )
    target = Path("notice/web/sample.pdf")
    doc.save(target, garbage=4, deflate=True)
    doc.close()


if __name__ == "__main__":
    main()
