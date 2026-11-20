# Application de recherche et d'analyse des impayés de crédit

Application web Streamlit destinée à l'analyse locale des rapports PDF de type **« SITUATION DES IMPAYES (RECAPITULATIF) »** (format de référence PERFECT V6.3). Elle prend en charge la recherche de clients, la consultation de tous leurs dossiers, les indicateurs de portefeuille, des graphiques interactifs, l'historique d'importation et les exports Excel/PDF.

## Fonctionnalités

- Sélection du répertoire de PDF depuis l'interface (chemin du poste qui exécute Streamlit).
- Parcours des PDF, y compris des sous-répertoires sur option.
- Extraction locale des lignes en utilisant les coordonnées des colonnes et conservation du fichier/page source.
- Recherche exacte ou partielle par nom, tolérance aux accents et recherche facultativement approximative.
- Fiche d'un client avec plusieurs dossiers et synthèse pour une date de situation donnée.
- KPI et graphiques Plotly : encours, impayés, intérêts, top clients, retard, concentration et tendance lorsqu'au moins deux dates sont disponibles.
- SQLite pour conserver les importations et l'historique localement.
- Vérification des totaux calculés par rapport à ceux imprimés lorsque le parseur les identifie.
- Export Excel, CSV et rapport PDF.
- Analyse assistée par OpenAI optionnelle, qui ne transmet les agrégats qu'après consentement explicite. Les noms des clients sont exclus de la requête IA par défaut.
- Application utilisable sans clé API OpenAI.

## Précaution de vocabulaire financier

Le rapport de référence porte une colonne **« Impayés »** et une colonne distincte **« Intérêts »**. L'application conserve les intitulés source et ne suppose pas silencieusement que « Impayés » signifie exclusivement « capital impayé ». Cette interprétation doit être validée par la direction financière ou le responsable métier avant de renommer le champ.

## Configuration requise

- Windows 10/11 ou environnement Python équivalent.
- Python 3.11 recommandé (une autre version peut fonctionner si toutes les dépendances sont compatibles).
- Droits de lecture sur le répertoire contenant les PDF.
- Internet uniquement nécessaire pour installer les dépendances et utiliser la fonctionnalité OpenAI.

## Installation sous Windows

1. Téléchargez et décompressez l'archive dans un dossier, par exemple `C:\Applications\credit_portfolio_app`.
2. Installez Python 3.11 ou supérieur depuis le site officiel de Python. Cochez **Add Python to PATH**.
3. Double-cliquez sur `INSTALLER.bat`. Ou ouvrez PowerShell dans le dossier du projet et exécutez :

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   Copy-Item .env.example .env
   ```

4. Modifiez le fichier `.env`. Au minimum, définissez un mot de passe robuste dans `APP_PASSWORD`. La clé OpenAI est facultative.
5. Double-cliquez sur `run.bat`, ou lancez :

   ```powershell
   .\.venv\Scripts\Activate.ps1
   streamlit run app.py
   ```

6. Ouvrez `http://localhost:8501` dans votre navigateur.

Si PowerShell refuse l'activation du script, vous pouvez utiliser `INSTALLER.bat` puis `run.bat`, qui s'appuient sur les scripts `.bat` de l'environnement virtuel.

## Utilisation

1. Ouvrez **Importation des PDF**.
2. Dans **Chemin du répertoire**, saisissez le chemin, par exemple `D:\Rapports_Credits\Impayes`.
3. Cliquez **Vérifier le répertoire** pour vérifier l'accès et visualiser les fichiers trouvés.
4. Cliquez **Analyser / actualiser les PDF**. La progression et le statut de chaque document sont affichés.
5. Consultez **Qualité et historique** pour vérifier les fichiers, les contrôles de totaux et les anomalies.
6. Dans **Recherche client**, sélectionnez une date de situation et recherchez le nom, une partie du nom ou un numéro de dossier.
7. Sélectionnez le nom exact si plusieurs clients correspondent. Consultez le détail et exportez les résultats au format CSV ou Excel.
8. Dans **Tableau de bord** et **Analyse du portefeuille**, consultez les indicateurs, graphiques et dossiers à examiner.

## Chemin du répertoire : local contre serveur

- **Application locale** : un chemin tel que `D:\Rapports_Credits\Impayes` désigne un dossier du même ordinateur que celui qui exécute Streamlit.
- **Serveur interne** : le chemin doit être accessible depuis le serveur. Pour un partage, utilisez un chemin réseau (UNC) et un compte de service ayant uniquement les droits nécessaires.
- **Serveur distant** : le serveur ne peut pas parcourir directement le disque personnel de l'utilisateur. Il faut téléverser les PDF ou les déposer dans un répertoire accessible au serveur.

Cette application est conçue d'abord pour un usage local ou intranet contrôlé. N'exposez pas le port Streamlit directement à Internet.

## Base de données et sauvegardes

Par défaut, SQLite est créée dans `data/credit_portfolio.sqlite3` après la première exécution. Pour déplacer la base, définissez `CREDIT_PORTFOLIO_DB` dans `.env` avec un chemin absolu. Le menu **Paramètres & déploiement** permet de télécharger une sauvegarde de la base locale.

Les fichiers importés sont identifiés par une empreinte SHA-256. Les fichiers inchangés ne sont pas retraités, sauf si l'option de forçage est cochée. Les contenus identiques trouvés à plusieurs chemins sont signalés et ne sont pas importés deux fois.

## OpenAI (facultatif)

1. Créez une clé API via votre compte autorisé.
2. Renseignez `OPENAI_API_KEY` dans `.env` (ne collez jamais la clé dans le code et ne la commitez pas dans Git).
3. Gardez le modèle dans `OPENAI_MODEL`, par défaut `gpt-5-mini`.
4. Dans **Analyse du portefeuille**, posez une question, lisez l'avertissement, puis cochez explicitement le consentement avant de demander une synthèse.
5. Par défaut, la requête contient les agrégats calculés localement et n'inclut pas le nom des clients. L'inclusion des noms nécessite une case distincte.

Les PDF entiers ne sont pas transmis automatiquement. Les chiffres sont calculés en Python; l'IA rédige une synthèse à partir des agrégats et ne doit jamais être utilisée comme source de vérité comptable.

## PDF scannés et formats différents

Le parseur est calibré sur la structure tabulaire du PDF de référence. Si un PDF est une image scannée, si ses colonnes sont déplacées ou s'il s'agit d'un autre rapport, l'extraction échouera ou peut nécessiter une adaptation. Cette version signale l'échec; elle n'active pas automatiquement l'OCR. Pour ajouter l'OCR, installez un moteur adapté (ex. Tesseract), validez les langues et ajoutez des tests sur un échantillon représentatif.

## Tests

Dans PowerShell, depuis le répertoire du projet :

```powershell
.\.venv\Scripts\Activate.ps1
pytest -q
```

Pour tester le PDF réel sans l'inclure dans l'archive, définissez d'abord une variable d'environnement qui pointe vers le fichier de test (sous PowerShell) :

```powershell
$env:REFERENCE_PDF = 'D:\Rapports_Credits\E_situat_impaye_recap.pdf'
pytest -q
```

Les tests qui nécessitent le PDF de référence sont automatiquement ignorés si `REFERENCE_PDF` n'est pas défini. Ne distribuez pas de rapports de clients comme fixtures de tests sans autorisation.

## Sécurité et limites

- Configurer `APP_PASSWORD` et garder l'application en local ou réseau interne autorisé.
- Streamlit n'est pas, à lui seul, une solution complète d'identité/SSO ou une protection suffisante pour un service exposé au public.
- Appliquer pare-feu, HTTPS, droits de répertoire, sauvegarde et contrôle d'accès en cas d'usage multi-utilisateur.
- Valider les conditions de confidentialité avant d'envoyer toute donnée à un service externe.
- Les rapports peuvent contenir des données personnelles et financières. Les données extraites sont stockées en clair dans SQLite par défaut : protéger les droits du dossier, le disque et les sauvegardes.
- Les contrôles automatiques de totaux ne garantissent pas que tous les champs ont été interprétés correctement; vérifier la source avant décision.
- Les lignes du même dossier pour différentes dates sont conservées comme historique. Les KPI de portefeuille sont calculés sur un instantané choisi pour éviter d'additionner les périodes.

## Dépannage

- **Aucun PDF trouvé** : vérifiez le chemin, le lecteur réseau et la case « Inclure les sous-répertoires ».
- **Accès refusé** : lancez l'application avec un compte disposant de droits de lecture sur le dossier.
- **Aucun dossier extrait** : document scanné, protégé ou format différent du modèle attendu; consulter le statut dans Qualité et historique.
- **Écart de total** : comparez les lignes/page source, le total imprimé et le périmètre de dossiers. Ne concluez pas automatiquement à une erreur comptable.
- **Erreur OpenAI** : vérifiez la clé, les droits sur le modèle, la connectivité et la facturation du compte API.
- **Port 8501 occupé** : lancez `streamlit run app.py --server.port 8502` puis ouvrez `http://localhost:8502`.
