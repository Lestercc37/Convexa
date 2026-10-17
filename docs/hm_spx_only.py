import csv, math, random, sys
sys.argv = ["x", "cone3"]
def wilson(h, n, z=1.96):
    p = h / n; den = 1 + z*z/n; c = (p + z*z/(2*n))/den; w = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))/den
    return 100*(c-w), 100*(c+w)
def sgn(x): return 1 if x > 0 else (-1 if x < 0 else 0)
def mins(t): return int(t[:2])*60 + int(t[3:5])
for label, path in (("0DTE only", "cone3/cone_gamma.csv"), ("0-2 DTE", "cone2/cone_gamma.csv")):
    rows = [r for r in csv.DictReader(open(path)) if r["symbol"] == "SPX"]
    print("\n=== SPX only |", label, "| samples", len(rows), "| days", len({r['day'] for r in rows}))
    for col, name in (("ret1", "5 min"), ("ret3", "15 min"), ("ret6", "30 min"), ("close", "to the close from ~11:00")):
        for regime in ("long", "short"):
            h = n = 0
            seen = {}
            sel = rows
            if col == "close":
                best = {}
                for r in rows:
                    d = abs(mins(r["time"]) - 660)
                    if d <= 5 and (r["day"] not in best or d < best[r["day"]][0]):
                        best[r["day"]] = (d, r)
                sel = [v[1] for v in best.values()]; c = "retclose"
            else:
                c = col
            for r in sel:
                ng = float(r["net_gamma"])
                if (regime == "long") != (ng >= 0) or r[c] == "": continue
                ret = float(r[c])
                if ret == 0: continue
                s, cen, mom = float(r["spot"]), float(r["centroid_total"]), float(r["past3"])
                p = sgn(cen - s) if regime == "long" else sgn(mom)
                if p == 0: continue
                n += 1; h += (p == sgn(ret))
            lo, hi = wilson(h, n) if n else (0, 0)
            print("  %-26s %-5s gamma | n=%3d hit %.1f%% (Wilson %.1f-%.1f)" % (name, regime, n, 100*h/max(n,1), lo, hi))
