"""Generate the shared frameless quill mark for web, Windows and Android."""
from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
PLUME = [('M', 80, 12), ('C', 57, 9, 37, 20, 28, 39),
         ('C', 24, 48, 24, 57, 29, 65), ('C', 39, 70, 53, 63, 62, 53),
         ('L', 52, 53), ('L', 70, 40), ('L', 60, 41), ('L', 77, 26),
         ('L', 69, 28), ('C', 76, 23, 79, 17, 80, 12), ('Z',)]
SPINE = [('M', 17, 83), ('C', 29, 62, 46, 41, 67, 25)]
BARBS = [[('M', 36, 51), ('L', 33, 40)], [('M', 47, 39), ('L', 45, 31)]]
ROSE, INK, LIGHT = '#BD7B9B', '#91536F', '#FFF7FC'


def path_data(commands):
    return ' '.join(command[0] + ' '.join(str(value) for value in command[1:]) for command in commands)


def points(commands, scale):
    result = []
    current = first = None
    for command, *values in commands:
        if command in ('M', 'L'):
            current = tuple(values)
            if first is None:
                first = current
            result.append(tuple(round(value * scale) for value in current))
        elif command == 'C':
            one, two, end = values[:2], values[2:4], values[4:]
            for index in range(1, 81):
                t = index / 80
                result.append(tuple(round(((1-t)**3 * current[k] + 3*(1-t)**2*t*one[k]
                                          + 3*(1-t)*t*t*two[k] + t**3*end[k])*scale) for k in (0, 1)))
            current = tuple(end)
        elif command == 'Z':
            result.append(tuple(round(value * scale) for value in first))
    return result


def build_assets():
    paths = [(PLUME, {'fill': ROSE}),
             *[(barb, {'fill': 'none', 'stroke': LIGHT, 'stroke-width': '2.2', 'stroke-linecap': 'round'}) for barb in BARBS],
             (SPINE, {'fill': 'none', 'stroke': INK, 'stroke-width': '3.6', 'stroke-linecap': 'round'})]
    svg = ET.Element('svg', xmlns='http://www.w3.org/2000/svg', viewBox='0 0 96 96', fill='none')
    ET.SubElement(svg, 'title').text = '未言诗集 · HushLeaf 羽毛笔标志'
    for commands, attributes in paths:
        ET.SubElement(svg, 'path', d=path_data(commands), **attributes)
    for relative in ('客户端/ui/assets/logo.svg', '服务端/admin_ui/logo.svg',
                     '服务器控制端/ui/logo.svg', '官网/logo.svg'):
        (ROOT / relative).write_text(ET.tostring(svg, encoding='unicode') + '\n', 'utf-8')

    android = 'http://schemas.android.com/apk/res/android'
    ET.register_namespace('android', android)
    attr = lambda name: '{' + android + '}' + name
    vector = ET.Element('vector', {attr('width'): '96dp', attr('height'): '96dp',
                                  attr('viewportWidth'): '96', attr('viewportHeight'): '96'})
    for commands, attributes in paths:
        values = {attr('pathData'): path_data(commands),
                  attr('fillColor'): '#00000000' if attributes['fill'] == 'none' else attributes['fill']}
        for name, key in (('stroke', 'strokeColor'), ('stroke-width', 'strokeWidth'), ('stroke-linecap', 'strokeLineCap')):
            if name in attributes:
                values[attr(key)] = attributes[name]
        ET.SubElement(vector, 'path', values)
    target = ROOT / '客户端/android/app/src/main/res/drawable/logo.xml'
    target.write_text(ET.tostring(vector, encoding='unicode') + '\n', 'utf-8')

    scale = 8
    image = Image.new('RGBA', (96 * scale, 96 * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.polygon(points(PLUME, scale), fill=ROSE)
    for commands, color, width in [(barb, LIGHT, 2.2) for barb in BARBS] + [(SPINE, INK, 3.6)]:
        line = points(commands, scale)
        draw.line(line, fill=color, width=round(width * scale), joint='curve')
        radius = width * scale / 2
        for x, y in (line[0], line[-1]):
            draw.ellipse((round(x-radius), round(y-radius), round(x+radius), round(y+radius)), fill=color)
    image = image.resize((256, 256), Image.Resampling.LANCZOS)
    image.save(ROOT / '客户端/ui/assets/logo.png')
    for folder in ('客户端/ui/assets', '服务端/admin_ui', '服务器控制端/ui'):
        image.save(ROOT / folder / 'logo.ico', sizes=[(16,16), (24,24), (32,32), (48,48), (64,64), (128,128), (256,256)])


if __name__ == '__main__':
    build_assets()
