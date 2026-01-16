# Racing Value Finder 🏇

A web-based racing analysis tool that identifies value picks, dud favourites, and market edges across Australian horse racing.

## Features

- **Value Picks** - Identifies horses where the model probability exceeds market implied probability
- **Dud Favourite Alerts** - Warns when favourites appear overrated by the market
- **Market Edge Detection** - Finds opportunities where combined bookmaker odds create favorable conditions
- **Dutching Calculator** - Calculate optimal stake distribution across multiple selections
- **Real-time Monitoring** - Track market edge opportunities with live updates

## Tech Stack

- **Backend**: Flask, Flask-SocketIO
- **Frontend**: Bootstrap 5, Socket.IO
- **Data**: Playwright for web scraping, Pandas for analysis
- **Deployment**: Railway-ready with Gunicorn

## Local Development

1. Clone the repository:
```bash
git clone https://github.com/yourusername/racing-value-finder.git
cd racing-value-finder
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
playwright install chromium
```

4. Run the application:
```bash
python app.py
```

5. Open http://localhost:5000 in your browser

## Deployment to Railway

1. Push to GitHub
2. Connect your Railway account to the repository
3. Railway will automatically detect the configuration and deploy

### Environment Variables (Optional)

- `PORT` - Server port (automatically set by Railway)
- `FLASK_DEBUG` - Set to `false` for production

## Project Structure

```
├── app.py                 # Main Flask application
├── templates/
│   └── index.html         # Frontend UI
├── racing/                # Core racing analysis modules
│   ├── config.py          # Configuration settings
│   ├── dutching.py        # Dutching calculations
│   ├── model.py           # Data models
│   ├── name_matcher.py    # Horse name matching
│   ├── odds_provider.py   # Odds data provider
│   ├── report.py          # Report generation
│   └── value_finder.py    # Value pick analysis
├── requirements.txt       # Python dependencies
├── Procfile              # Heroku/Railway process file
├── railway.json          # Railway configuration
└── nixpacks.toml         # Nixpacks build configuration
```

## License

MIT License
