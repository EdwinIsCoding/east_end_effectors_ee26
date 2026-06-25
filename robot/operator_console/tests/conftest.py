import pathlib
import sys

# Make `robot.operator_console` importable when pytest is run from anywhere.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
