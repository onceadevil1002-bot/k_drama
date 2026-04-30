import pypdfium2 as pdfium

pdf_path = "output/pdf/kdrama_bot_command_guide.pdf"
out_dir = "tmp/pdfs"

pdf = pdfium.PdfDocument(pdf_path)
for index in range(len(pdf)):
    page = pdf[index]
    bitmap = page.render(scale=1.4).to_pil()
    output = f"{out_dir}/command_guide_page_{index + 1}.png"
    bitmap.save(output)
    print(output, bitmap.size)
