"""Vector QR codes with a quiet zone and small, high-contrast purpose icons."""
from reportlab.graphics.shapes import Drawing, Group, Rect, Path, Polygon, Line
from reportlab.lib.colors import HexColor, white


def qr_drawing(url, size, kind='book', color='#000000'):
    from reportlab.graphics.barcode.qr import QrCodeWidget
    ink = HexColor(color)
    drawing = Drawing(size, size)
    # The white quiet zone stays opaque even over a transparent ending panel.
    drawing.add(Rect(0, 0, size, size, fillColor=white, strokeColor=None))
    drawing.add(QrCodeWidget(url, barWidth=size, barHeight=size, barBorder=4,
                            barLevel='H', barFillColor=ink, barStrokeColor=ink))
    badge = Group(transform=(size / 100, 0, 0, size / 100, 0, 0))
    badge.add(Rect(40, 40, 20, 20, rx=2, ry=2, fillColor=white, strokeColor=None))
    if kind == 'video':
        badge.add(Rect(42, 44, 16, 12, rx=2, ry=2, fillColor=ink, strokeColor=None))
        badge.add(Polygon([48, 46.5, 48, 53.5, 54, 50], fillColor=white, strokeColor=None))
    else:
        book = Path(fillColor=white, strokeColor=ink, strokeWidth=1.2, strokeLineJoin=1)
        book.moveTo(50, 44)
        book.curveTo(47, 46, 44, 46, 42, 45)
        book.lineTo(42, 56)
        book.curveTo(45, 57, 48, 56, 50, 54)
        book.curveTo(52, 56, 55, 57, 58, 56)
        book.lineTo(58, 45)
        book.curveTo(55, 46, 52, 46, 50, 44)
        book.closePath()
        badge.add(book)
        badge.add(Line(50, 44, 50, 54, strokeColor=ink, strokeWidth=1.2))
    drawing.add(badge)
    return drawing


def qr_svg(url, kind='book', color='#000000'):
    svg = qr_drawing(url, 160, kind, color).asString('svg')
    if isinstance(svg, bytes): svg = svg.decode('utf-8')
    return svg[svg.index('<svg'):svg.rindex('</svg>') + 6]
