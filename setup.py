import csv
from pathlib import Path

from setuptools import setup, Command

import config

with open('requirements.txt', 'r') as f:
    REQUIREMENTS = f.read().splitlines()


class StartCommand(Command):

    """Start the web server and setup logging."""

    description = 'start the web server'
    user_options = []

    def initialize_options(self) -> None:
        pass

    def finalize_options(self) -> None:
        pass

    def run(self) -> None:
        # prepare logfiles
        if not Path(config.log_settings["ce_logfile"]).is_file():  # if logfile does not already exist
            with open(config.log_settings["ce_logfile"], "w") as logfile:  # create new logfile
                writer = csv.writer(logfile, delimiter=config.log_settings["csv_delimiter"])
                writer.writerow(config.log_settings["ce_logfile_columns"])  # write header to logfile
                #writer.writerow(["2000-01-01 00:00:00", "<--- LAST INSERTED INTO DB"])
        # run application
        import uvicorn
        uvicorn.run('src.main:CATModule')


class CreateDbCommand(Command):

    """Create the database."""

    description = 'create the database'
    user_options = []

    def initialize_options(self) -> None:
        pass

    def finalize_options(self) -> None:
        pass

    def run(self) -> None:
        from src.cat.db_connector import engine
        from src.models.sqlalchemy_models import Base
        from src.models import sqlalchemy_models
        Base.metadata.create_all(engine)


class InitDatabase(Command):

    """
    Initialize Alembic Migrations.
    Use only if Alembic Migrations has not been initialized yet (no migrations folder, no alembic.ini).
    """

    description = 'initialize alembic migrations'
    user_options = []

    def initialize_options(self) -> None:
        pass

    def finalize_options(self) -> None:
        pass

    def run(self) -> None:
        import os
        os.system("alembic init migrations")


class MigrateCommand(Command):

    """
    Create Migration file.
    Autogenerate does not detect all changes (e.g. table/column name changes).
    Therefore, checking the migration file (and making changes if necessary) before upgrading is highly recommended.
    """

    description = 'create migration file'
    user_options = []

    def initialize_options(self) -> None:
        pass

    def finalize_options(self) -> None:
        pass

    def run(self) -> None:
        import os
        os.system("alembic revision --autogenerate")


class UpgradeCommand(Command):

    """Apply changes to database."""

    description = 'upgrade database'
    user_options = []

    def initialize_options(self) -> None:
        pass

    def finalize_options(self) -> None:
        pass

    def run(self) -> None:
        import os
        os.system("alembic upgrade heads")

setup(
    name='CAT-Module',
    version='0.0.1',
    description='Adaptive testing engine',
    long_description='A backend for adaptive and classical online-tests in GeoGebra.',
    url='https://git.geogebra.org/mathskill/cat-module',
    license='Apache 2.0',
    py_modules=['src'],
    python_requires='>=3.7',
    install_requires=REQUIREMENTS,
    cmdclass={
        'start': StartCommand,
        'create_database': CreateDbCommand,
        'init_db': InitDatabase,
        'migrate': MigrateCommand,
        'upgrade': UpgradeCommand
    },
    classifiers=[
        # See https://pypi.org/classifiers/
        'License :: OSI Approved :: Apache Software License',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.7'
    ],
)
