NIGHTMARES Music v3.0
=====================

NOUVEAUTES v3.0
- Resultats illimites (50 a 1000, discographie artiste)
- Onglet Parametres (connexion, yt-dlp, ffmpeg)
- Cles API pour ton bot WhatsApp
- Endpoints /api/v1/* proteges par X-API-Key

LANCEMENT
  source ~/music-venv/bin/activate   # si tu utilises le venv
  cd NIGHTMARES-Music
  python server.py
  -> http://127.0.0.1:8765

TUNNEL (partager avec des potes)
  cloudflared tunnel --url http://127.0.0.1:8765

API BOT
  1) Ouvre Parametres -> Creer une cle API
  2) Dans ton bot:
     Header: X-API-Key: nm_xxxx

  GET /api/v1/search?q=Damso&limit=100&artist=1
  GET /api/v1/download?q=Damso+Macarena&title=Macarena&artist=Damso&format=file
  GET /api/v1/lyrics?artist=Damso&title=Macarena
  GET /api/v1/ping

Exemple Node (Baileys):
  const r = await fetch(BASE + "/api/v1/download?q=..." +
    "&title=...&artist=...&format=file", {
    headers: { "X-API-Key": process.env.NM_API_KEY }
  });
  const buf = Buffer.from(await r.arrayBuffer());
  // sock.sendMessage(jid, { audio: buf, mimetype: "audio/mpeg" })
