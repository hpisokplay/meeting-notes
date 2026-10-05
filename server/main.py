import os
from pathlib import Path

import uvicorn

from .app import create_app

app = create_app(Path(os.environ.get("MEETINGS_DATA", "/data")))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8778")))
