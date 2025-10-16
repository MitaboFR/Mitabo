from flask import Flask, render_template_string

app = Flask(name)

TEMPLATE = """ <!doctype html>

<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Règlement Officiel — Mitabo</title>
  <style>
    body { font-family: Inter, Roboto, Arial, sans-serif; background: #f7f8fa; color: #111; margin: 0; padding: 24px; }
    .container { max-width: 900px; margin: 32px auto; background: #fff; border-radius: 8px; box-shadow: 0 6px 18px rgba(17,17,17,0.06); padding: 28px; }
    header h1 { margin: 0 0 8px 0; font-size: 24px; letter-spacing: 0.2px; }
    .meta { color: #6b7280; font-size: 13px; margin-bottom: 18px; }
    hr { border: none; border-top: 1px solid #e6e9ee; margin: 22px 0; }
    h2 { font-size: 18px; margin: 18px 0 8px; }
    p, li { line-height: 1.55; font-size: 15px; }
    ol { padding-left: 1.2em; }
    .article { margin-bottom: 12px; }
    .foot { color: #6b7280; font-size: 13px; margin-top: 18px; }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>Règlement Officiel de Mitabo</h1>
      <div class="meta">Version officielle — Ton administratif</div>
    </header><section class="article">
  <h2>Article 1 – Objet du règlement</h2>
  <p>Le présent règlement a pour objet de définir les conditions de publication, de diffusion et d’utilisation de la plateforme <strong>Mitabo</strong>. Il vise à assurer un environnement respectueux, créatif et conforme à la législation en vigueur.</p>
</section>

<hr />

<section class="article">
  <h2>Article 2 – Format et durée des vidéos</h2>
  <ol>
    <li>Les vidéos publiées sur Mitabo doivent avoir une durée comprise entre <strong>3 et 5 minutes</strong>.</li>
    <li>Le format recommandé est horizontal (16:9) avec une qualité minimale de 720p (HD).</li>
    <li>Les vidéos doivent être montées, finalisées et conformes aux standards de qualité avant leur mise en ligne.</li>
  </ol>
</section>

<hr />

<section class="article">
  <h2>Article 3 – Contenu autorisé</h2>
  <p>Sont autorisés :</p>
  <ul>
    <li>Les créations originales (documentaires, tutoriels, vlogs, courts-métrages, etc.) ;</li>
    <li>Les contenus respectueux de la loi, des droits d’auteur et de la dignité des personnes ;</li>
    <li>Les musiques et extraits sous licence libre ou disposant d’une autorisation d’utilisation.</li>
  </ul>
</section>

<hr />

<section class="article">
  <h2>Article 4 – Contenu interdit</h2>
  <p>Sont formellement interdits :</p>
  <ul>
    <li>Les propos ou images à caractère haineux, violent, discriminatoire ou diffamatoire ;</li>
    <li>Les contenus mensongers, trompeurs ou incitant à des comportements dangereux ;</li>
    <li>La diffusion de données personnelles sans consentement préalable ;</li>
    <li>Toute forme de plagiat ou d’atteinte aux droits d’autrui.</li>
  </ul>
</section>

<hr />

<section class="article">
  <h2>Article 5 – Comportement des utilisateurs</h2>
  <p>Les utilisateurs de Mitabo doivent :</p>
  <ul>
    <li>Adopter une attitude respectueuse envers la communauté et l’équipe de modération ;</li>
    <li>Publier et commenter de manière constructive et courtoise ;</li>
    <li>Signaler tout contenu non conforme au présent règlement.</li>
  </ul>
</section>

<hr />

<section class="article">
  <h2>Article 6 – Sanctions</h2>
  <p>Tout manquement au présent règlement pourra entraîner :</p>
  <ol>
    <li>Un avertissement écrit adressé à l’utilisateur concerné ;</li>
    <li>Une suspension temporaire du compte en cas de récidive ;</li>
    <li>Une exclusion définitive en cas de manquement grave ou répété.</li>
  </ol>
  <p>Les décisions de modération sont prises avec impartialité et dans le respect du droit d’expression de chacun.</p>
</section>

<hr />

<section class="article">
  <h2>Article 7 – Entrée en vigueur</h2>
  <p>Le présent règlement entre en vigueur à compter de sa publication officielle sur la plateforme Mitabo. Toute utilisation du service implique l’acceptation sans réserve des dispositions énoncées ci‑dessus.</p>
</section>

<div class="foot">Document généré automatiquement — Mitabo</div>

  </div>
</body>
</html>
"""@app.route('/') def index(): return render_template_string(TEMPLATE)

@app.route('/reglement') def reglement(): return render_template_string(TEMPLATE)

if name == 'main': 
