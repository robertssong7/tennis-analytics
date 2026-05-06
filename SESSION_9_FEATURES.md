# SESSION 9 — AI-POWERED FEATURES: UPSET RISK + SURFACE DNA + MATCH NARRATIVE
# Autonomous. No confirmations. Execute sequentially.
# Commit after EVERY phase. If stuck 3 tries → skip, document, continue.

## DO NOT MODIFY:
- modules/ (any file)
- scripts/ (any file)
- config.js
- requirements.txt

## PRODUCTION:
- API: https://su7vqmgkbd.us-east-1.awsapprunner.com
- Frontend: https://tennisiq-one.vercel.app (Vercel)
- CORS: allow_origins=["*"]

## DESIGN SYSTEM (MANDATORY):
- Page bg: #F5F0EB
- Cards: #FFFFFF, shadow 0 2px 8px rgba(0,0,0,0.08), border-radius 12px
- Text: #2C2C2C
- Accent: #0ABAB5 (Tiffany blue / Legendary)
- Gold tier: #DAA520, Silver: #A8A9AD, Bronze: #CD7F32
- Hard: #4A90D9, Clay: #D4724E, Grass: #5AA469
- Heading font: Playfair Display
- Body font: DM Sans
- Data/mono font: DM Mono (if needed for numbers)
- Bar track: #D0C9C0, bar height: 8px
- Tooltip: #2C2C2C bg, white text, 12px DM Sans
- No emojis. Flags acceptable.
- No gradients on cards.
- No bold mid-sentence. Bold for headings/labels only.

---

## PHASE 0: READ EVERYTHING FIRST

```bash
cd ~/Documents/tennis-analytics

# Read all relevant backend code
cat src/api/main.py
cat src/api/predict_engine.py

# Read all frontend pages
cat frontend/public/dashboard/index.html
cat frontend/public/dashboard/player.html
cat frontend/public/dashboard/compare.html

# Check what match-insight returns (this is the data foundation)
lsof -ti:8000 | xargs kill -9 2>/dev/null
python3 -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 &
sleep 20

curl -s "http://localhost:8000/api/match-insight" -X POST \
  -H "Content-Type: application/json" \
  -d '{"player1":"Sinner","player2":"Alcaraz","surface":"hard"}' | python3 -m json.tool

curl -s "http://localhost:8000/api/match-insight" -X POST \
  -H "Content-Type: application/json" \
  -d '{"player1":"Djokovic","player2":"Shelton","surface":"hard"}' | python3 -m json.tool

curl -s "http://localhost:8000/predict/player/Nadal" | python3 -m json.tool

curl -s "http://localhost:8000/player/Nadal/conditions" | python3 -m json.tool | head -30

curl -s "http://localhost:8000/player/Nadal/matchups" | python3 -m json.tool | head -20

lsof -ti:8000 | xargs kill -9 2>/dev/null
```

RECORD ALL OUTPUTS. You need the exact response shapes before writing any code.

---

## PHASE 1: UPSET RISK SCORE

### 1.1 Backend: Add upset_risk to match-insight response

In `src/api/main.py`, find the `/api/match-insight` endpoint. BEFORE the return statement,
compute the upset risk score and add it to the response.

**Upset Risk Formula (0-100):**
```python
# Base risk from probability
base_risk = dog_prob * 100  # 0-50 range (underdog can't be >50%)

# Adjustment factors
adjustments = 0

# 1. Close Elo = higher risk (within 100 points = very risky)
if elo_diff < 50:
    adjustments += 15
elif elo_diff < 100:
    adjustments += 10
elif elo_diff < 200:
    adjustments += 5

# 2. H2H favors underdog = higher risk
if h2h_wins_1 + h2h_wins_2 > 0:
    dog_h2h = h2h_wins_1 if underdog == p1 else h2h_wins_2
    fav_h2h = h2h_wins_2 if underdog == p1 else h2h_wins_1
    if dog_h2h > fav_h2h:
        adjustments += 12  # Underdog LEADS the H2H
    elif dog_h2h == fav_h2h:
        adjustments += 6   # Even H2H

# 3. Underdog has better form
dog_form = f3_1 if underdog == p1 else f3_2
fav_form = f3_2 if underdog == p1 else f3_1
if dog_form > fav_form + 0.2:
    adjustments += 8
elif dog_form > fav_form:
    adjustments += 4

# 4. Surface favors underdog
dog_surf = surf_ratings.get(underdog, 80)
fav_surf = surf_ratings.get(favorite, 80)
if dog_surf > fav_surf:
    adjustments += 10  # Underdog is BETTER on this surface

# 5. Attribute advantages for underdog
if len(dog_advantages) >= 2:
    adjustments += 8
elif len(dog_advantages) >= 1:
    adjustments += 4

# Combine: scale base_risk (0-50) + adjustments into 0-100
upset_risk = min(100, int(base_risk * 1.5 + adjustments))

# Generate one-line explanation
if upset_risk >= 75:
    risk_label = "High upset potential"
    risk_detail = f"{underdog} has multiple edges that could flip this match."
elif upset_risk >= 50:
    risk_label = "Moderate upset risk"
    risk_detail = f"{underdog} has real paths to winning — don't sleep on this one."
elif upset_risk >= 30:
    risk_label = "Low but possible"
    risk_detail = f"{favorite} is clearly favored, but {underdog} isn't helpless."
else:
    risk_label = "Heavy favorite"
    risk_detail = f"{favorite} dominates across nearly every dimension."

# Add specific reason
if dog_advantages:
    best = dog_advantages[0]
    risk_detail += f" Watch for {underdog}'s {best['attr']}."
```

Add to the return dict:
```python
"upset_risk": {
    "score": int(upset_risk),
    "label": risk_label,
    "detail": risk_detail
}
```

### 1.2 Frontend: Show upset risk on compare page

In `compare.html`, after the win probability bar section, add an upset risk badge:

```html
<div id="upset-risk-badge" style="display:none;text-align:center;margin:16px 0;">
  <div style="display:inline-flex;align-items:center;gap:10px;padding:10px 20px;background:#FFFFFF;border-radius:20px;box-shadow:0 2px 8px rgba(0,0,0,0.08);">
    <div id="risk-circle" style="width:44px;height:44px;border-radius:50%;display:flex;align-items:center;justify-content:center;">
      <span id="risk-score" style="font-family:'DM Mono',monospace;font-size:16px;font-weight:600;color:#fff;"></span>
    </div>
    <div>
      <div id="risk-label" style="font-family:'DM Sans',sans-serif;font-size:14px;font-weight:600;color:#2C2C2C;"></div>
      <div id="risk-detail" style="font-family:'DM Sans',sans-serif;font-size:12px;color:#2C2C2C;opacity:0.6;max-width:300px;"></div>
    </div>
  </div>
</div>
```

JavaScript to populate (after the match-insight fetch in compare.html):
```javascript
function renderUpsetRisk(data) {
    var badge = document.getElementById('upset-risk-badge');
    if (!data || !data.upset_risk) { badge.style.display = 'none'; return; }
    
    var risk = data.upset_risk;
    var circle = document.getElementById('risk-circle');
    var score = risk.score;
    
    // Color by risk level
    var color = score >= 75 ? '#E24B4A' :   // red — high risk
                score >= 50 ? '#EF9F27' :   // amber — moderate
                score >= 30 ? '#DAA520' :   // gold — low
                '#5AA469';                   // green — heavy favorite
    
    circle.style.background = color;
    document.getElementById('risk-score').textContent = score;
    document.getElementById('risk-label').textContent = risk.label;
    document.getElementById('risk-detail').textContent = risk.detail;
    badge.style.display = 'block';
}
```

### 1.3 Frontend: Show upset risk in today's matchups on index.html

In the match insight cards on the homepage, add the upset risk score as a colored badge
in the top-right corner of each card:

```javascript
// Inside the match insight card HTML builder
var riskScore = d.upset_risk ? d.upset_risk.score : 0;
var riskColor = riskScore >= 75 ? '#E24B4A' : riskScore >= 50 ? '#EF9F27' : riskScore >= 30 ? '#DAA520' : '#5AA469';
var riskBadge = d.upset_risk ? 
    '<div style="position:absolute;top:12px;right:12px;background:'+riskColor+';color:#fff;'
    +'border-radius:12px;padding:3px 10px;font-family:DM Mono,monospace;font-size:11px;font-weight:600;">'
    +'Upset '+riskScore+'</div>' : '';
```

Add `position:relative;` to the card container div and insert riskBadge at the top.

CHECKPOINT: match-insight response includes upset_risk. Compare page shows colored circle badge. Homepage matchup cards show upset score.

```bash
git add -A && git commit -m "Add Upset Risk Score — 0-100 scale with formula, shown on compare page and homepage matchups"
```

---

## PHASE 2: SURFACE DNA PROFILE

### 2.1 Backend: New endpoint GET /player/{name}/surface-dna

Add to `src/api/main.py`:

```python
@app.get("/player/{name}/surface-dna")
async def player_surface_dna(name: str):
    """
    Generate a Surface DNA profile — how a player's identity changes across surfaces.
    Returns per-surface analysis with narrative text.
    """
    matched = engine.name_match(name)
    if not matched:
        return {"available": False, "reason": "Player not found"}
    
    card = engine.get_player_card(matched)
    if not card:
        return {"available": False, "reason": "Player data unavailable"}
    
    surfaces_data = card.get("surfaces", {})
    attributes = card.get("attributes", {})
    overall = float(card.get("overall", 0))
    
    # Get conditions data for each surface
    conditions_by_surface = {}
    for surf in ["hard", "clay", "grass"]:
        try:
            # Reuse the conditions logic from the existing endpoint
            # Check how _get_player_conditions works and call it
            pass
        except:
            pass
    
    # Build per-surface profiles
    profiles = {}
    best_surface = max(surfaces_data.items(), key=lambda x: x[1]) if surfaces_data else ("hard", overall)
    worst_surface = min(surfaces_data.items(), key=lambda x: x[1]) if surfaces_data else ("grass", overall)
    
    surface_names = {"hard": "Hard Court", "clay": "Clay Court", "grass": "Grass Court"}
    surface_colors = {"hard": "#4A90D9", "clay": "#D4724E", "grass": "#5AA469"}
    
    for surf, rating in surfaces_data.items():
        diff_from_overall = float(rating) - overall
        
        # Generate narrative based on rating difference
        if diff_from_overall > 3:
            identity = "thrives"
            narrative = f"This is where {matched.split()[1] if len(matched.split())>1 else matched} elevates. "
        elif diff_from_overall > 0:
            identity = "comfortable"
            narrative = f"A solid surface that suits {matched.split()[1] if len(matched.split())>1 else matched}'s game. "
        elif diff_from_overall > -3:
            identity = "neutral"
            narrative = f"Neither an advantage nor a liability. "
        else:
            identity = "vulnerable"
            narrative = f"A surface that exposes weaknesses. "
        
        # Add attribute-based insight
        if surf == "clay":
            endurance = float(attributes.get("endurance", 50))
            groundstroke = float(attributes.get("groundstroke", 50))
            if endurance > 80:
                narrative += f"High endurance ({int(endurance)}) helps grind through long clay rallies. "
            if groundstroke > 75:
                narrative += f"Strong groundstrokes ({int(groundstroke)}) provide the heavy topspin clay demands."
            elif groundstroke < 55:
                narrative += f"Groundstroke rating ({int(groundstroke)}) may struggle against clay-court baseliners."
        elif surf == "grass":
            serve = float(attributes.get("serve", 50))
            volley = float(attributes.get("volley", 50))
            if serve > 75:
                narrative += f"Big serve ({int(serve)}) translates well to fast grass conditions. "
            if volley > 60:
                narrative += f"Net skills ({int(volley)}) allow effective serve-and-volley tactics."
            elif volley < 40:
                narrative += f"Limited net game ({int(volley)}) means relying on baseline play even on grass."
        elif surf == "hard":
            mental = float(attributes.get("mental", 50))
            clutch = float(attributes.get("clutch", 50))
            if mental > 75 and clutch > 75:
                narrative += f"Mental strength ({int(mental)}) and clutch play ({int(clutch)}) thrive in hard-court pressure points."
            elif serve > 70:
                narrative += f"Serve ({int(attributes.get('serve', 50))}) anchors the game on the sport's most common surface."
        
        profiles[surf] = {
            "surface": surf,
            "surface_name": surface_names.get(surf, surf),
            "rating": float(rating),
            "diff_from_overall": round(float(diff_from_overall), 1),
            "identity": identity,
            "narrative": narrative.strip(),
            "color": surface_colors.get(surf, "#A8A9AD")
        }
    
    # Overall DNA summary
    spread = float(best_surface[1]) - float(worst_surface[1])
    if spread < 3:
        dna_type = "All-Court"
        dna_summary = f"{matched} performs consistently across all surfaces. No clear weakness to exploit, no standout surface to target."
    elif best_surface[0] == "clay":
        dna_type = "Clay Specialist"
        dna_summary = f"{matched}'s game is built for clay — patience, topspin, and endurance define the identity. Other surfaces require adaptation."
    elif best_surface[0] == "grass":
        dna_type = "Grass Specialist"  
        dna_summary = f"{matched} comes alive on grass. The fast, low-bouncing conditions reward the aggressive, serve-dominant style."
    elif best_surface[0] == "hard":
        dna_type = "Hard Court Specialist"
        dna_summary = f"{matched} is most dangerous on hard courts — the neutral surface rewards the complete, well-rounded game."
    else:
        dna_type = "Balanced"
        dna_summary = f"{matched} shows reasonable comfort across surfaces."
    
    return {
        "available": True,
        "player": matched,
        "dna_type": dna_type,
        "dna_summary": dna_summary,
        "overall_rating": float(overall),
        "best_surface": {"surface": best_surface[0], "rating": float(best_surface[1])},
        "worst_surface": {"surface": worst_surface[0], "rating": float(worst_surface[1])},
        "spread": round(float(spread), 1),
        "profiles": profiles
    }
```

### 2.2 Frontend: Add Surface DNA section to player.html

Add AFTER the Player Attributes section and BEFORE Conditions.

```html
<div class="section" id="surface-dna-section" style="display:none;">
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
        <span style="color:#0ABAB5;font-family:'DM Sans',sans-serif;font-size:11px;font-weight:600;letter-spacing:2px;text-transform:uppercase;">Surface intelligence</span>
        <div style="flex:1;height:1px;background:#D0C9C0;"></div>
    </div>
    <h3 style="font-family:'Playfair Display',serif;font-size:24px;color:#2C2C2C;margin:0 0 4px;">Surface DNA</h3>
    <div id="dna-type" style="font-family:'DM Sans',sans-serif;font-size:14px;color:#0ABAB5;font-weight:600;margin-bottom:8px;"></div>
    <div id="dna-summary" style="font-family:'DM Sans',sans-serif;font-size:14px;color:#2C2C2C;opacity:0.7;margin-bottom:20px;"></div>
    <div id="dna-profiles" style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;"></div>
</div>
```

Each surface profile card:
```javascript
function renderSurfaceDNA(data) {
    var section = document.getElementById('surface-dna-section');
    if (!data || !data.available) { section.style.display = 'none'; return; }
    
    document.getElementById('dna-type').textContent = data.dna_type;
    document.getElementById('dna-summary').textContent = data.dna_summary;
    
    var container = document.getElementById('dna-profiles');
    var html = '';
    
    var surfaceOrder = ['hard', 'clay', 'grass'];
    surfaceOrder.forEach(function(surf) {
        var p = data.profiles[surf];
        if (!p) return;
        
        var diffSign = p.diff_from_overall > 0 ? '+' : '';
        var diffColor = p.diff_from_overall > 0 ? '#5AA469' : p.diff_from_overall < -2 ? '#E24B4A' : '#2C2C2C';
        
        html += '<div style="background:#FFFFFF;border-radius:12px;padding:16px;border-top:3px solid '+p.color+';">'
            + '<div style="font-family:DM Sans,sans-serif;font-size:13px;font-weight:600;color:'+p.color+';text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">'+p.surface_name+'</div>'
            + '<div style="display:flex;align-items:baseline;gap:6px;margin-bottom:8px;">'
            + '<span style="font-family:Playfair Display,serif;font-size:28px;color:#2C2C2C;">'+p.rating.toFixed(1)+'</span>'
            + '<span style="font-family:DM Mono,monospace;font-size:12px;color:'+diffColor+';">'+diffSign+p.diff_from_overall.toFixed(1)+'</span>'
            + '</div>'
            + '<div style="font-family:DM Sans,sans-serif;font-size:12px;color:#2C2C2C;opacity:0.7;line-height:1.5;">'+p.narrative+'</div>'
            + '</div>';
    });
    
    container.innerHTML = html;
    section.style.display = 'block';
}
```

Call it from the player page data loading function:
```javascript
fetch(API_URL + '/player/' + encodeURIComponent(playerName) + '/surface-dna')
    .then(r => r.ok ? r.json() : null)
    .then(data => { if(data) renderSurfaceDNA(data); })
    .catch(() => {});
```

CHECKPOINT: Surface DNA section renders on player profile with 3 colored cards (hard/clay/grass), each showing rating, diff, and narrative.

```bash
git add -A && git commit -m "Add Surface DNA Profile — per-surface identity analysis on player dashboard"
```

---

## PHASE 3: MATCH NARRATIVE GENERATOR

### 3.1 Backend: New endpoint POST /api/match-narrative

This takes the structured match-insight data and generates analyst-quality prose.
Template-based v1 — no external API needed.

Add to `src/api/main.py`:

```python
@app.post("/api/match-narrative")
async def match_narrative(request: Request):
    """
    Generate an analyst-style match narrative from structured insight data.
    Reads like ESPN commentary from Andy Roddick or Darren Cahill.
    """
    body = await request.json()
    p1_raw = body.get("player1", "")
    p2_raw = body.get("player2", "")
    surface = body.get("surface", "hard")
    
    # Get match insight first
    p1 = engine.name_match(p1_raw)
    p2 = engine.name_match(p2_raw)
    if not p1 or not p2:
        return {"available": False, "reason": "Player not found"}
    
    # Reuse match-insight logic (or call the function internally)
    # Get the same data that /api/match-insight returns
    # For code reuse, extract the insight logic into a helper function
    # OR just call the match_insight endpoint internally
    
    try:
        pred = engine.predict(p1, p2, surface)
    except:
        return {"available": False, "reason": "Prediction failed"}
    
    p1_prob = float(pred.get("p1_win_prob", pred.get("stacked_prob", 0.5)))
    card1 = engine.get_player_card(p1)
    card2 = engine.get_player_card(p2)
    if not card1 or not card2:
        return {"available": False, "reason": "Player data unavailable"}
    
    # Extract key data points
    p1_last = p1.split()[-1]  # Last name for natural prose
    p2_last = p2.split()[-1]
    elo1 = float(card1.get("elo", 1500))
    elo2 = float(card2.get("elo", 1500))
    attrs1 = card1.get("attributes", {})
    attrs2 = card2.get("attributes", {})
    form1 = card1.get("form", {})
    form2 = card2.get("form", {})
    surfs1 = card1.get("surfaces", {})
    surfs2 = card2.get("surfaces", {})
    
    f3_1 = float(form1.get("form_3", 0.5))
    f3_2 = float(form2.get("form_3", 0.5))
    
    favorite = p1 if p1_prob >= 0.5 else p2
    underdog = p2 if p1_prob >= 0.5 else p1
    fav_last = favorite.split()[-1]
    dog_last = underdog.split()[-1]
    fav_prob = max(p1_prob, 1 - p1_prob)
    
    fav_attrs = attrs1 if favorite == p1 else attrs2
    dog_attrs = attrs2 if favorite == p1 else attrs1
    fav_surfs = surfs1 if favorite == p1 else surfs2
    dog_surfs = surfs2 if favorite == p1 else surfs1
    fav_form = f3_1 if favorite == p1 else f3_2
    dog_form = f3_2 if favorite == p1 else f3_1
    
    surface_name = {"hard": "hard court", "clay": "clay", "grass": "grass"}.get(surface, surface)
    
    # === BUILD THE NARRATIVE ===
    paragraphs = []
    
    # PARAGRAPH 1: The Setup
    elo_diff = abs(elo1 - elo2)
    if fav_prob > 0.70:
        opener = f"This is {fav_last}'s match to lose."
        if elo_diff > 200:
            opener += f" A {int(elo_diff)}-point Elo gap tells you everything about the class difference here."
        else:
            opener += f" The model gives {fav_last} a commanding {fav_prob*100:.0f}% edge on {surface_name}."
    elif fav_prob > 0.55:
        opener = f"Slight edge to {fav_last}, but this is far from a foregone conclusion."
        opener += f" At {fav_prob*100:.0f}-{(1-fav_prob)*100:.0f}, the margins are thin enough that one service break could flip the script."
    else:
        opener = f"Throw the rankings out — this is a genuine coin-flip."
        opener += f" {p1_last} and {p2_last} are separated by just {int(elo_diff)} Elo points, and the model sees it at {p1_prob*100:.0f}-{(1-p1_prob)*100:.0f}."
    paragraphs.append(opener)
    
    # PARAGRAPH 2: The Key Matchup Dynamic
    # Find the most interesting attribute mismatch
    biggest_gap = None
    biggest_gap_val = 0
    for attr in ["serve", "groundstroke", "endurance", "mental", "clutch"]:
        v1 = float(fav_attrs.get(attr, 50))
        v2 = float(dog_attrs.get(attr, 50))
        gap = abs(v1 - v2)
        if gap > biggest_gap_val:
            biggest_gap_val = gap
            biggest_gap = {"attr": attr, "fav_val": int(v1), "dog_val": int(v2), "favors": "favorite" if v1 > v2 else "underdog"}
    
    if biggest_gap and biggest_gap_val > 10:
        attr_name = biggest_gap["attr"]
        if biggest_gap["favors"] == "favorite":
            dynamic = f"The matchup hinges on {attr_name}. {fav_last} holds a clear {biggest_gap['fav_val']}-to-{biggest_gap['dog_val']} advantage there"
            if attr_name == "serve":
                dynamic += " — expect free points on the first serve and pressure in return games."
            elif attr_name == "endurance":
                dynamic += " — if this goes three sets, the fitness edge becomes decisive."
            elif attr_name == "mental":
                dynamic += " — in the big moments, tiebreaks and break points, that mental edge separates the professionals from the pretenders."
            elif attr_name == "groundstroke":
                dynamic += f" — on {surface_name}, that baseline superiority should dictate rallies."
            elif attr_name == "clutch":
                dynamic += " — when the pressure peaks, clutch players find a way. That's the difference-maker."
            else:
                dynamic += "."
        else:
            dynamic = f"Here's what makes this interesting: {dog_last} actually outscores {fav_last} in {attr_name}, {biggest_gap['dog_val']} to {biggest_gap['fav_val']}."
            dynamic += f" If {dog_last} can steer the match into a {attr_name}-heavy battle, the upset window cracks open."
    else:
        dynamic = f"These two mirror each other across the stat sheet — no clear technical mismatch to exploit."
        dynamic += f" It comes down to who executes better on the day."
    paragraphs.append(dynamic)
    
    # PARAGRAPH 3: The Surface Factor
    fav_surf_rating = float(fav_surfs.get(surface, 80))
    dog_surf_rating = float(dog_surfs.get(surface, 80))
    surf_diff = fav_surf_rating - dog_surf_rating
    
    if abs(surf_diff) > 5:
        better = fav_last if surf_diff > 0 else dog_last
        worse = dog_last if surf_diff > 0 else fav_last
        surface_para = f"The {surface_name} surface tilts this. {better} rates {max(fav_surf_rating, dog_surf_rating):.1f} here versus {min(fav_surf_rating, dog_surf_rating):.1f} for {worse}."
        if surface == "clay":
            surface_para += f" Clay rewards patience and topspin — {better}'s game translates better to the slow, high-bouncing conditions."
        elif surface == "grass":
            surface_para += f" Grass is about first-strike tennis — serve, slice, get to the net. {better} is more equipped for that style."
        else:
            surface_para += f" Hard court is the great equalizer in tennis, but even here, the numbers favor {better}."
    elif abs(surf_diff) < 2:
        surface_para = f"Surface is a non-factor — both players rate within {abs(surf_diff):.1f} points on {surface_name}. This one will be decided by execution, not conditions."
    else:
        surface_para = f"Slight {surface_name} edge to {fav_last if surf_diff > 0 else dog_last}, but not enough to be a decisive factor."
    paragraphs.append(surface_para)
    
    # PARAGRAPH 4: The Prediction
    if fav_prob > 0.70:
        prediction = f"The call: {fav_last} in straight sets."
        prediction += f" The gap is too wide across too many dimensions for {dog_last} to overcome in a best-of-three."
        prediction += f" {dog_last} will have moments — maybe a break in the first set — but {fav_last} has the tools to reset and close."
    elif fav_prob > 0.58:
        prediction = f"The call: {fav_last} in three sets."
        prediction += f" {dog_last} has enough game to take a set, and the margins suggest this will be competitive throughout."
        prediction += f" But {fav_last}'s edge in the key areas should prove just enough to close it out."
    else:
        prediction = f"The call: pick'em, but lean {fav_last}."
        prediction += f" This is a match where form on the day matters more than any stat line."
        
        # Add form context
        if fav_form > dog_form + 0.15:
            prediction += f" {fav_last}'s recent form ({int(fav_form*3)}-of-3 recent wins) provides the tiebreaker."
        elif dog_form > fav_form + 0.15:
            prediction += f" But watch out — {dog_last} is actually in better recent form. This could easily go the other way."
        else:
            prediction += f" Both are in similar form. Expect a war."
    paragraphs.append(prediction)
    
    return {
        "available": True,
        "player1": p1,
        "player2": p2,
        "surface": surface,
        "narrative": paragraphs,
        "favorite": favorite,
        "underdog": underdog,
        "fav_prob": float(fav_prob)
    }
```

### 3.2 Frontend: Add narrative button to compare.html

After the prediction section in compare.html, add a "Match Analysis" button that fetches
and displays the narrative:

```html
<div id="narrative-section" style="max-width:700px;margin:24px auto;display:none;">
    <button id="narrative-btn" onclick="loadNarrative()" style="
        display:block;width:100%;padding:12px;background:#2C2C2C;color:#F5F0EB;
        border:none;border-radius:8px;cursor:pointer;
        font-family:'DM Sans',sans-serif;font-size:14px;font-weight:500;
        margin-bottom:16px;transition:opacity 0.2s;">
        Generate Match Analysis
    </button>
    <div id="narrative-content" style="display:none;"></div>
</div>
```

```javascript
async function loadNarrative() {
    var btn = document.getElementById('narrative-btn');
    btn.textContent = 'Analyzing...';
    btn.style.opacity = '0.5';
    
    try {
        var r = await fetch(API_URL + '/api/match-narrative', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({player1: currentP1, player2: currentP2, surface: currentSurface})
        });
        var data = await r.json();
        
        if (data.available && data.narrative) {
            var html = '<div style="background:#FFFFFF;border-radius:12px;padding:24px;box-shadow:0 2px 8px rgba(0,0,0,0.08);">';
            html += '<div style="font-family:DM Sans,sans-serif;font-size:11px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#0ABAB5;margin-bottom:12px;">Match Analysis</div>';
            
            data.narrative.forEach(function(para) {
                html += '<p style="font-family:DM Sans,sans-serif;font-size:14px;color:#2C2C2C;line-height:1.7;margin:0 0 12px;">' + para + '</p>';
            });
            
            html += '</div>';
            document.getElementById('narrative-content').innerHTML = html;
            document.getElementById('narrative-content').style.display = 'block';
            btn.textContent = 'Regenerate Analysis';
            btn.style.opacity = '1';
        }
    } catch(e) {
        btn.textContent = 'Generate Match Analysis';
        btn.style.opacity = '1';
    }
}
```

### 3.3 Frontend: Add narrative to player.html match history

In the player profile's matchups section, add a small "Analyze" button next to each
toughest/easiest opponent that generates a narrative for that specific matchup:

For each matchup row, append:
```javascript
'<span class="analyze-btn" onclick="analyzeMatchup(\''+opponent+'\')" style="'
+'font-family:DM Sans,sans-serif;font-size:11px;color:#0ABAB5;cursor:pointer;'
+'font-weight:500;margin-left:8px;opacity:0.7;">Analyze</span>'
```

The `analyzeMatchup` function opens a modal or inline expansion showing the narrative.

CHECKPOINT: Compare page has "Generate Match Analysis" button producing 4-paragraph analysis. Player page matchups have "Analyze" links.

```bash
git add -A && git commit -m "Add Match Narrative Generator — template-based analyst commentary for any matchup"
```

---

## PHASE 4: TEST ALL FEATURES LOCALLY

```bash
lsof -ti:8000 | xargs kill -9 2>/dev/null
python3 -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 &
sleep 20

echo "=== UPSET RISK ==="
curl -s "http://localhost:8000/api/match-insight" -X POST \
  -H "Content-Type: application/json" \
  -d '{"player1":"Sinner","player2":"Alcaraz","surface":"hard"}' | python3 -c "
import sys,json; d=json.load(sys.stdin)
ur = d.get('upset_risk', {})
print(f'Score: {ur.get(\"score\")}, Label: {ur.get(\"label\")}')
print(f'Detail: {ur.get(\"detail\")}')
"

echo "=== SURFACE DNA ==="
curl -s "http://localhost:8000/player/Nadal/surface-dna" | python3 -c "
import sys,json; d=json.load(sys.stdin)
print(f'Type: {d.get(\"dna_type\")}')
print(f'Summary: {d.get(\"dna_summary\")}')
for s,p in d.get('profiles',{}).items():
    print(f'  {s}: {p.get(\"rating\")} ({p.get(\"identity\")}) — {p.get(\"narrative\")[:60]}...')
"

echo "=== MATCH NARRATIVE ==="
curl -s "http://localhost:8000/api/match-narrative" -X POST \
  -H "Content-Type: application/json" \
  -d '{"player1":"Djokovic","player2":"Shelton","surface":"hard"}' | python3 -c "
import sys,json; d=json.load(sys.stdin)
for i,p in enumerate(d.get('narrative',[])):
    print(f'[{i+1}] {p[:100]}...')
"

echo "=== ALL ENDPOINT STATUS ==="
for ep in health predict/player/Sinner player/Sinner/matchups player/Sinner/surface-dna; do
    code=\$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/\$ep)
    echo "\$ep: \$code"
done

lsof -ti:8000 | xargs kill -9 2>/dev/null
```

CHECKPOINT: All three features return valid data. Nadal's Surface DNA shows "Clay Specialist."

---

## PHASE 5: COMMIT, PUSH, DEPLOY

```bash
cd ~/Documents/tennis-analytics
git add -A
git status
git commit -m "Session 9 — Upset Risk Score, Surface DNA Profile, Match Narrative Generator"
/usr/bin/git push origin main
cd frontend/public/dashboard && npx vercel --prod --yes
```

Wait for AWS rebuild:
```bash
sleep 420

echo "=== PRODUCTION VERIFICATION ==="
curl -s "https://su7vqmgkbd.us-east-1.awsapprunner.com/player/Nadal/surface-dna" | python3 -c "
import sys,json; d=json.load(sys.stdin)
print(f'DNA Type: {d.get(\"dna_type\")}')
"

curl -s "https://su7vqmgkbd.us-east-1.awsapprunner.com/api/match-narrative" -X POST \
  -H "Content-Type: application/json" \
  -d '{"player1":"Sinner","player2":"Alcaraz","surface":"hard"}' | python3 -c "
import sys,json; d=json.load(sys.stdin)
print(f'Narrative paragraphs: {len(d.get(\"narrative\",[]))}')
print(d.get('narrative',[''])[0][:100])
"
```

---

## ANTI-STALL RULES
1. Do NOT modify modules/, scripts/, config.js, requirements.txt
2. Re-read files before editing
3. Use /usr/bin/git for push
4. Commit after every phase
5. Wrap ALL numeric returns with float()/int()
6. ALL frontend fetch calls must use API_URL from config.js
7. No emojis in UI
8. Test with json.dumps() after building any response dict
9. The narrative templates should sound like ESPN analysts, not like an AI
10. If an attribute value is 0 or missing, handle gracefully — don't let it crash the narrative
11. Surface DNA narratives should be specific and insightful, not generic
12. "Explore the Dashboards" must remain the last section before footer on index.html
