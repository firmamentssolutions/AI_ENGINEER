# Rapport de validation du projet

## Contrôles exécutés

- Compilation syntaxique : `python -W error::SyntaxWarning -m compileall -q .` — réussie.
- Tests automatisés avec le PDF de référence configuré dans `REFERENCE_PDF` — **11 tests réussis**.
- Test du PDF de référence `E_situat_impaye_recap.pdf` : 4 pages, 86 dossiers extraits.
- Sommes calculées comparées aux totaux imprimés :
  - Montant crédit : 172 923 000 FCFA — correspondance exacte.
  - Encours des dossiers impayés : 71 486 402 FCFA — correspondance exacte.
  - Impayés (libellé source) : 46 109 047 FCFA — correspondance exacte.
  - Intérêts : 4 130 357 FCFA — correspondance exacte.
- Test d'importation du répertoire : 1 rapport importé, 86 lignes stockées.
- Test de détection de contenu dupliqué : une copie identique est signalée et n'ajoute pas de dossiers.
- Test de réimportation : un document inchangé est signalé comme « Déjà à jour ».
- Tests d'export : génération d'un classeur XLSX et d'un rapport PDF testée.

## Limite de validation de l'interface

L'application Streamlit n'a pas été lancée dans l'environnement de création, car le module Streamlit n'y était pas installé et l'environnement ne pouvait pas télécharger le paquet depuis PyPI. Le code de l'interface a été compilé syntaxiquement, mais un test visuel interactif complet doit être réalisé après installation des dépendances sur le poste Windows cible.

## Avertissements métier

Le PDF emploie les colonnes « Impayés » et « Intérêts ». Le programme conserve les libellés source et propose une sélection d'interprétation de la colonne « Impayés » ; l'institution doit confirmer avant d'utiliser le libellé « Capital impayé ». Le PDF de référence n'est pas inclus dans l'archive pour éviter de redistribuer des données financières de clients.
