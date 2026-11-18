"""Measure the ink boxes used for the markers on pages/patent_front.png (no text layer on the scan).

Prints, in image pixels of the 1600 x 1278 crop: the six title lines of (54), the words of the first
(75) Inventors line and of the (73) Assignee line, and the extent of the (75)-(73) block.
patent_front.html and sec_collage.html use these numbers.
"""
import os

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
im = Image.open(os.path.join(HERE, 'pages', 'patent_front.png')).convert('L')
px = im.load()
INK = 140


def rows(x0, x1, y0, y1):
    runs, cur = [], None
    for y in range(y0, y1):
        ink = any(px[x, y] < INK for x in range(x0, x1))
        if ink and cur is None:
            cur = [y, y]
        elif ink:
            cur[1] = y
        elif cur:
            runs.append(tuple(cur))
            cur = None
    if cur:
        runs.append(tuple(cur))
    return runs


def cols(x0, x1, y0, y1):
    xs = [x for x in range(x0, x1) if any(px[x, y] < INK for y in range(y0, y1 + 1))]
    return (min(xs), max(xs)) if xs else None


def words(x0, x1, y0, y1, gap=6):
    out, cur, blank = [], None, 0
    for x in range(x0, x1):
        ink = any(px[x, y] < INK for y in range(y0, y1 + 1))
        if ink:
            if cur is None:
                cur = [x, x]
            elif blank >= gap:
                out.append(tuple(cur))
                cur = [x, x]
            else:
                cur[1] = x
            blank = 0
        else:
            blank += 1
    if cur:
        out.append(tuple(cur))
    return out


print('(54) title lines (top, bottom, x0, x1):')
for a, b in rows(262, 780, 300, 470):
    print('  ', a, b, *cols(262, 780, a, b))
print('(75) first inventor line words:', words(370, 790, 492, 512))
print('(73) assignee line words:', words(370, 790, 568, 588))
print('(75)-(73) block ink: x', cols(262, 800, 485, 620), 'y', rows(262, 800, 485, 620)[0][0], rows(262, 800, 485, 620)[-1][1])
