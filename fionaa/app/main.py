"""AgentCore bootstrap for its source archive (the wheel needs no bootstrap)."""
from pathlib import Path
import site

# AgentCore packages dependencies and source but does not install this project.
# Add the archive's src directory, independent of the process working directory.
site.addsitedir(str(Path(__file__).resolve().parent / "src"))

from fionaa.main import app

if __name__ == "__main__":
    app.run()
