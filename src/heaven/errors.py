"""Exceptions du projet."""


class HeavenError(Exception):
    """Erreur de base : toute erreur attendue hérite de celle-ci."""


class ConfigError(HeavenError):
    """Fichier de configuration absent, illisible ou invalide."""


class RepositoryError(HeavenError):
    """Dépôt de sauvegarde absent, corrompu ou dans une version inconnue."""


class SnapshotNotFoundError(HeavenError):
    """L'instantané demandé n'existe pas dans le dépôt."""


class IntegrityError(HeavenError):
    """Le contenu stocké ne correspond pas à l'empreinte attendue."""
