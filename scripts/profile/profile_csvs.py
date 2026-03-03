"""
CSV Profiler — Checkpoint 1
Scans all CSVs in data/raw/charting/, produces data/manifest/auto_profile.json
"""
import os, json, csv

RAW_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'raw', 'charting')
OUT_FILE = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'manifest', 'auto_profile.json')

def infer_type(values):
    """Infer column type from a sample of non-empty values."""
    for v in values:
        if v == '' or v is None:
            continue
        try:
            int(v)
            continue
        except ValueError:
            pass
        try:
            float(v)
            return 'float'
        except ValueError:
            return 'string'
    # All parsed as int or were empty
    has_int = any(v != '' and v is not None for v in values)
    return 'int' if has_int else 'string'

def profile_csv(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        headers = next(reader)
        headers = [h.strip() for h in headers]

        # Read all rows to count + sample
        rows = []
        for row in reader:
            rows.append(row)

        total_rows = len(rows)
        sample_rows = rows[:5]

        # Infer types from first 100 rows
        type_sample = rows[:100]
        col_types = {}
        for i, h in enumerate(headers):
            vals = [r[i] if i < len(r) else '' for r in type_sample]
            col_types[h] = infer_type(vals)

        # Build sample as list of dicts
        samples = []
        for row in sample_rows:
            d = {}
            for i, h in enumerate(headers):
                d[h] = row[i] if i < len(row) else ''
            samples.append(d)

        has_match_id = 'match_id' in headers

    return {
        'filename': os.path.basename(filepath),
        'total_rows': total_rows,
        'num_columns': len(headers),
        'headers': headers,
        'column_types': col_types,
        'has_match_id': has_match_id,
        'sample_rows': samples
    }

def main():
    files = sorted([f for f in os.listdir(RAW_DIR) if f.endswith('.csv')])
    print(f"Found {len(files)} CSV files in {RAW_DIR}\n")

    profiles = {}
    for f in files:
        path = os.path.join(RAW_DIR, f)
        p = profile_csv(path)
        short = f.replace('charting-m-stats-', '').replace('.csv', '')
        profiles[short] = p
        print(f"  {short}: {p['total_rows']} rows, {p['num_columns']} cols, match_id={'✓' if p['has_match_id'] else '✗'}")

    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(profiles, f, indent=2)

    print(f"\n✅ Wrote {OUT_FILE}")

if __name__ == '__main__':
    main()
