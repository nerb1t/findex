from findex.cli import app

# захист обов'язковий для ProcessPoolExecutor зі spawn (Windows/macOS):
# процес-воркер заново імпортує головний модуль як "__mp_main__", і без if
# кожен воркер знову запускав би CLI і створював свій пул - нескінченно
if __name__ == "__main__":
    app()
