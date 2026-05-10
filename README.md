# Transcript Podcasts

Webapp locale qui résume un document (texte, PDF, audio) ou un lien (YouTube, podcast),
puis te laisse discuter avec un chatbot du contenu.

## Stack

- **Voxtral mini** (`voxtral-mini-latest`) — speech-to-text Mistral (audio uniquement)
- **Mistral OCR** (`mistral-ocr-latest`) — extraction PDF (gère scans + texte natif, sortie markdown)
- **Ministral 8B** (`ministral-8b-latest`) — résumé + chat
- **FastAPI** (Python) backend, frontend HTML/CSS/JS vanilla (aucun build)
- **yt-dlp** pour l'ingestion par URL

## Pré-requis

- Python ≥ 3.12
- [uv](https://docs.astral.sh/uv/) pour la gestion du projet Python
- `ffmpeg` — `brew install ffmpeg`
- `deno` — `brew install deno` (requis pour résoudre le n-sig challenge YouTube)

## Installation

```bash
uv sync
```

## Lancer le serveur

```bash
export MISTRAL_API_KEY=ta_cle_mistral
export YTDLP_COOKIES_FROM_BROWSER=firefox   # optionnel, voir ci-dessous
uv run uvicorn main:app --reload
```

Puis ouvre <http://127.0.0.1:8000>.

## Variables d'environnement

| Variable | Obligatoire | Description |
|---|---|---|
| `MISTRAL_API_KEY` | ✅ | Clé API Mistral |
| `YTDLP_COOKIES_FROM_BROWSER` | ❌ | Navigateur dont yt-dlp doit lire les cookies. Valeurs : `firefox`, `safari`, `chrome`, `brave`, `edge`, `opera`, `vivaldi`, `chromium`. Quasi-indispensable pour YouTube depuis qu'ils ont durci leur anti-bot. |

## Sources d'entrée supportées

### Fichiers (drag & drop)
- **Texte** : `.txt`, `.md`, `.pdf`
- **Audio** : `.mp3`, `.wav`, `.m4a`, `.flac`, `.ogg`, `.webm`, `.mp4`, `.mpeg`, `.mpga`

### URLs
- ✅ **YouTube** (avec cookies — voir dépannage)
- ✅ **Apple Podcasts**, **SoundCloud**, **flux RSS direct**, sites de podcasts (BFM, France Culture, etc.) — `yt-dlp` supporte ~1800 sites
- ❌ **Deezer** — refusé par yt-dlp à cause du DRM, même pour les podcasts qui sont en pratique du MP3 public
- ❌ **Spotify musique** — DRM

#### Si Deezer ne marche pas

C'est attendu. Le même podcast est presque toujours disponible ailleurs :

1. **Apple Podcasts** (`podcasts.apple.com`) — meilleure couverture pour les podcasts FR
2. **Le site officiel du show** (BFM Business, France Inter, France Culture…)
3. **Flux RSS** — beaucoup de podcasts l'exposent ; sinon cherche sur [podcastindex.org](https://podcastindex.org)
4. **Spotify Podcasts** (épisodes non-exclusifs uniquement)

Colle l'URL d'épisode trouvée sur l'une de ces plateformes dans le champ URL de la webapp.

## Dépannage

### YouTube : "Sign in to confirm you're not a bot"

YouTube détecte qu'on n'a pas de session authentifiée. Définis
`YTDLP_COOKIES_FROM_BROWSER` avec un navigateur où tu es déjà connecté à YouTube
et redémarre le serveur.

- **Firefox** est le plus simple sur Mac (cookies en clair, pas de garde système).
- **Safari** : les cookies sont protégés par macOS TCC. Va dans
  `Réglages Système → Confidentialité et sécurité → Accès complet au disque`,
  ajoute Terminal (ou iTerm), relance ton terminal.
- **Chrome / Brave** : ça demandera ton mot de passe macOS (keychain) au démarrage.

### YouTube : "Requested format is not available"

C'est le **n-sig challenge** : YouTube sert un JS qu'il faut exécuter pour
calculer un paramètre nécessaire au téléchargement. Il faut `deno` (déjà
installé via `brew install deno`) et le package `yt-dlp[default]` (déjà
dans les dépendances). Vérifie :

```bash
which deno
uv run yt-dlp --cookies-from-browser firefox -F "URL_DE_LA_VIDEO"
```

Tu dois voir une vraie liste de formats audio (m4a, webm), pas juste des
storyboards.

### Erreur API Mistral 401

Ta `MISTRAL_API_KEY` est invalide ou n'a pas accès aux modèles utilisés.

### Aucune transcription produite

Voxtral n'a rien trouvé dans le fichier audio (silence, format corrompu).
Vérifie que le fichier se lit normalement.

## Architecture

```
main.py                       # backend FastAPI
static/
  index.html                  # SPA (upload + chat)
  app.js                      # logique frontend
  style.css                   # thème dark
data/conversations.json       # persistance (créé automatiquement)
pyproject.toml                # dépendances uv
```

### Endpoints

| Méthode | Chemin | Description |
|---|---|---|
| `POST` | `/api/process` | Upload de fichier → transcription/extraction → résumé → renvoie un `document_id` |
| `POST` | `/api/process-url` | URL → download audio (yt-dlp) → transcription → résumé |
| `POST` | `/api/chat` | Tour de chat sur un `document_id` |
| `GET` | `/api/documents` | Liste des conversations |
| `GET` | `/api/documents/{id}` | Détail d'une conversation (résumé + historique) |
| `DELETE` | `/api/documents/{id}` | Supprime une conversation |

### Stratégie de coût pour le chat

Pour éviter de re-payer un long document à chaque tour de chat :

- Document ≤ 4000 caractères → **texte complet** dans le system prompt (négligeable)
- Sinon → **résumé seul** dans le system prompt

Modifiable via la constante `FULL_TEXT_INLINE_THRESHOLD` dans `main.py`.

### Persistance

Les conversations (texte source, résumé, messages) sont stockées en JSON dans
`data/conversations.json`, écrit après chaque mutation. Pour repartir de zéro :
supprime ce fichier (ou utilise le bouton "Supprimer" dans l'UI conversation par
conversation).
