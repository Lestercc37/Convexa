import csv, math, random
M = 98280.0
def rms(zs): return math.sqrt(sum(z*z for z in zs)/len(zs))
for label, path in (("0DTE-only gamma", "cone3/cone_gamma.csv"), ("0-2 DTE gamma", "cone2/cone_gamma.csv")):
    rows = [r for r in csv.DictReader(open(path)) if r["sig_real"] != ""]
    print("\n===", label)
    for sym in ("SPX", "ALL"):
        for col, mins in (("ret1", 5), ("ret3", 15), ("ret6", 30)):
            g = {"long": [], "short": []}
            for r in rows:
                if sym != "ALL" and r["symbol"] != sym: continue
                if r[col] == "": continue
                z = float(r[col]) / (float(r["sig_real"]) * math.sqrt(mins / M))
                g["long" if float(r["net_gamma"]) >= 0 else "short"].append((r["day"], z))
            a, b = [z for _, z in g["long"]], [z for _, z in g["short"]]
            ratio = rms(b) / rms(a)
            # bootstrap over days for the ratio short/long
            days = sorted({d for d, _ in g["long"] + g["short"]}); by = {}
            for d, z in g["long"]: by.setdefault(d, ([], []))[0].append(z)
            for d, z in g["short"]: by.setdefault(d, ([], []))[1].append(z)
            rng = random.Random(3); rs = []
            for _ in range(1000):
                A, B = [], []
                for _ in days:
                    x = by[rng.choice(days)]; A += x[0]; B += x[1]
                if A and B: rs.append(rms(B) / rms(A))
            rs.sort()
            print("  %-3s %2d min | long gamma n=%4d RMS z %.2f | short gamma n=%4d RMS z %.2f | short/long %.2f (by-day bootstrap %.2f-%.2f)" % (sym, mins, len(a), rms(a), len(b), rms(b), ratio, rs[25], rs[974]))
