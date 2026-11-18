"""Cut the real-document crops used by the figures.

Reads the SEC order PDF in docs/ and writes grayscale PNG crops to ./assets/.
Needs PyMuPDF (pip install pymupdf). Run from anywhere: python make_assets.py
"""
import json
import os

import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
ORDER = os.path.join(DOCS, "sec_order_2017_33-10280.pdf")
OUT = os.path.join(HERE, "assets")

# (page index, clip in PDF points, zoom, file name)
CROPS = [
    # page 1: the caption, "In the Matter of Citadel Securities LLC", Release No. 10280
    (0, (60, 90, 552, 424), 3.0, "sec_order_p1_caption.png"),
    # page 11, Section V, items B and C: censure and the three amounts ordered
    (10, (66, 331, 542, 410), 4.0, "sec_order_p11_payment.png"),
]
AMOUNTS = ["$5,200,000", "$1,465,268", "$16,000,000"]


def main():
    os.makedirs(OUT, exist_ok=True)
    doc = pymupdf.open(ORDER)
    boxes = {}
    for pno, clip, zoom, name in CROPS:
        page = doc[pno]
        rect = pymupdf.Rect(*clip)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=rect,
                              colorspace=pymupdf.csGRAY, alpha=False)
        pix.save(os.path.join(OUT, name))
        print(name, pix.width, "x", pix.height)
        if pno == 10:
            for a in AMOUNTS:
                r = page.search_for(a)[0]
                boxes[a] = {  # position inside the crop, in % of its width / height
                    "left": round((r.x0 - rect.x0) / rect.width * 100, 2),
                    "top": round((r.y0 - rect.y0) / rect.height * 100, 2),
                    "width": round(r.width / rect.width * 100, 2),
                    "height": round(r.height / rect.height * 100, 2),
                }
    with open(os.path.join(OUT, "sec_order_p11_amounts.json"), "w") as f:
        json.dump(boxes, f, indent=1)
    print(json.dumps(boxes))


if __name__ == "__main__":
    main()
