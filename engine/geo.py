"""Point-in-polygon lookup against a GeoJSON file of named areas (standard library only)."""
import json


def _inside(x, y, ring):
    hit = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            hit = not hit
        j = i
    return hit


class Locator:
    """Answers "which named area contains this point?"."""

    def __init__(self, path, name_key):
        with open(path, encoding="utf-8") as f:
            features = json.load(f)["features"]
        self.shapes = []
        for feat in features:
            geom = feat["geometry"]
            polygons = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
            for rings in polygons:
                outer = rings[0]
                xs, ys = [p[0] for p in outer], [p[1] for p in outer]
                self.shapes.append((min(xs), min(ys), max(xs), max(ys), rings, feat["properties"][name_key]))

    def find(self, lat, lon):
        for x0, y0, x1, y1, rings, name in self.shapes:
            if x0 <= lon <= x1 and y0 <= lat <= y1 and _inside(lon, lat, rings[0]) and not any(_inside(lon, lat, hole) for hole in rings[1:]):
                return name
        return None
