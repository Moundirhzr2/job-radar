# Job Radar : plan du projet

Document de référence : le produit, les règles, les sources, l'architecture et l'avancement.
À mettre à jour à chaque décision.

## Le principe

**Moins de candidatures, envoyées aux bonnes personnes.** Sur les grands sites d'emploi, on
postule partout et on n'obtient pas de réponse. Job Radar fait l'inverse : il montre ce qui
recrute autour de soi, y compris les entreprises qui n'ont rien publié, et le chemin le plus
direct vers la personne qui décide.

Pour qui : les étudiants qui cherchent un **stage**, une **alternance**, un **emploi** ou un
**job étudiant**. Premier utilisateur : Moundir, pour sa propre recherche. Ensuite : sa promotion
à l'UHA.

## Le parcours

1. **Régler le radar.** Un centre (adresse, université, ville), un rayon réglable (jusqu'à
   50 km au départ), le type de contrat recherché et le domaine.
2. **Deux couches sur la carte.**
   - Les **offres publiées** dans le rayon.
   - Les **entreprises qui recrutent sans publier** (marché caché), avec leur secteur et leur
     taille.
3. **Pour chaque entreprise, le chemin vers la source**, du plus officiel au plus personnel :
   1. le contact publié par l'employeur dans son offre ;
   2. les canaux de recrutement de l'entreprise (page carrières, adresse de recrutement,
      téléphone), avec le lien vers la page où ils ont été trouvés ;
   3. pour les petites entreprises, le nom du dirigeant (registre public des entreprises) ;
   4. des recherches LinkedIn en un clic : recruteurs, responsable de l'équipe visée, **anciens
      de ton université** qui y travaillent (lien ouvert chez l'utilisateur, aucune collecte) ;
   5. pour l'alternance, la candidature transmise directement au recruteur par l'API officielle
      de La bonne alternance, sans que l'adresse soit affichée.
4. **Ce qui te manque pour cette offre.** Comparaison de l'offre et du CV en trois groupes :
   - ✅ présent sur le CV ;
   - 🟡 probablement acquis mais absent du CV : l'outil **demande** avant de proposer de
     l'ajouter ;
   - 🔴 pas encore acquis : un **mini-projet** (1 à 2 week-ends, données publiques, dépôt GitHub
     avec README) qui prouve la compétence, relié si possible au secteur de l'entreprise.
   - À l'échelle du radar : la compétence manquante qui débloquerait le plus d'offres.
5. **Fiche entreprise et brouillon de message (RAG).** Lecture des pages publiques de
   l'entreprise, fiche courte où chaque fait renvoie à sa source, puis un brouillon de message
   fondé sur cette fiche et le CV. L'utilisateur le réécrit et l'envoie lui-même.
6. **Suivi.** Qui a été contacté, par quel canal, quand relancer, qui a répondu. Taux de
   réponse par canal : la preuve chiffrée (ou non) que « aller à la source » marche mieux.

## Les règles du projet

1. Uniquement des **API officielles** et des données que les entreprises publient pour être
   lues par des machines.
2. **Aucun scraping** de sites d'emploi (LinkedIn, Indeed, Welcome to the Jungle, HelloWork…) :
   leurs conditions l'interdisent et leurs bases sont protégées par le droit des bases de
   données. **Aucune collecte d'adresses e-mail personnelles** : la CNIL a sanctionné KASPR de
   240 000 € en 2024 pour une collecte de ce type.
3. Sur les sites des entreprises : respect du fichier `robots.txt`, requêtes lentes, pas de copie
   intégrale des offres, **lien systématique vers la page d'origine**.
4. **Jamais d'invention sur un CV.** L'outil peut proposer une formulation honnête
   (« en cours d'apprentissage, voir projet X »), jamais une compétence non acquise.
5. **Pas de candidature automatique.** L'outil mène à la bonne page et prépare un brouillon ;
   l'utilisateur relit, réécrit et envoie.
6. Les **données de l'utilisateur** (CV, suivi) lui appartiennent : privées, supprimables sur
   demande, jamais partagées entre utilisateurs.

## Les sources

| Source | Apporte | Accès |
|---|---|---|
| France Travail – Offres d'emploi v2 | Offres (emploi, alternance, temps partiel), y compris celles de sites partenaires. Recherche par commune + rayon | Gratuit, compte francetravail.io, OAuth2 (client credentials), 10 appels/s |
| France Travail – La Bonne Boîte | Entreprises classées par potentiel d'embauche pour un métier (ROME) ou une activité (NAF) et un lieu | Même compte francetravail.io |
| La bonne alternance | Offres d'alternance, entreprises qui recrutent en alternance, transmission de candidature au recruteur | Gratuit, usage non commercial |
| API Recherche d'entreprises | Toutes les entreprises autour d'un point (`/near_point`, rayon ≤ 50 km), secteur, taille, dirigeants | Gratuit, sans clé, 7 requêtes/s |
| API Géo (geo.api.gouv.fr) | Commune → coordonnées et code INSEE | Gratuit, sans clé |
| Pages carrières des entreprises | Offres publiées en données structurées schema.org `JobPosting` (le format lu par Google pour ses résultats d'emploi) | Données publiées pour être lues ; respect de robots.txt |
| Flux publics des logiciels de recrutement | Greenhouse, Lever, SmartRecruiters, Recruitee : flux d'offres prévus pour la diffusion | Publics, sans clé |

Point faible connu : **peu d'offres de stage** dans les API publiques (1jeune1solution en affiche
plus de 20 000 mais ne documente pas d'API). Pour les stages, l'outil s'appuie surtout sur la
couche entreprises et le contact direct.

## Architecture

- **Pipeline Python** : collecte quotidienne des sources, normalisation dans un modèle d'offre
  commun, dédoublonnage entre sources, géocodage.
- **PostgreSQL** (Supabase en production) avec **PostGIS** (recherches par distance) et
  **pgvector** (recherche sémantique).
- **Claude** : extraction des compétences en données structurées, fiches entreprises sourcées,
  analyse d'écart CV/offre, brouillons de message.
- **Application web** avec la carte et le radar (choix du framework à faire : Streamlit pour
  aller vite, ou Next.js pour un vrai produit multi-utilisateurs).
- **GitHub Actions** : tests à chaque push, collecte planifiée.

## La partie RAG : comment elle sera construite

Un RAG utilisable en vrai ne se limite pas à « chercher puis générer ». Chaque étape est
mesurée avant qu'on fasse confiance à une réponse.

1. **Réécriture de la requête.** Avant d'atteindre l'index, Claude transforme la demande et le
   profil (« stage data près de Mulhouse » + CV) en une recherche structurée : termes et
   synonymes FR/EN (analyste de données, BI, SQL, Power BI…), types de contrat, rayon.
2. **Recherche hybride.** pgvector (sens) + plein texte français sans accents (mots exacts) +
   filtre de distance PostGIS, fusionnés par Reciprocal Rank Fusion dans une seule requête SQL
   (moteur repris du dépôt `Rag`).
3. **Re-ranking.** Les 50 premiers résultats sont réordonnés par un modèle qui lit chaque paire
   (profil, offre). Deux candidats comparés sur les mesures : un reranker multilingue local et
   Claude en juge de pertinence.
4. **Mesure avant confiance.**
   - Jeu d'évaluation : environ 50 offres étiquetées « pertinente / non pertinente » par Moundir.
   - Mesures par étape : rappel@50 après la recherche, nDCG@10 et MRR après le re-ranking.
   - Tableau d'ablation : chaque étape activée ou non, pour savoir ce que chacune apporte.
   - Seuil de confiance : si le meilleur score est trop faible, l'outil dit « pas assez d'offres
     pertinentes » au lieu de répondre.
   - Pour les fiches entreprises : chaque affirmation doit citer un passage, et un contrôle
     automatique vérifie que le passage cité existe dans la source.

## Feuille de route

| Étape | Résultat | Statut |
|---|---|---|
| 1 | Base PostgreSQL + PostGIS + pgvector, requête radar | Fait |
| 2 | Lecture des sites carrières : JobPosting, flux ATS, sitemaps, robots.txt | Fait, testé sur données réelles |
| 3 | Entreprises autour d'un point (annuaire officiel), dirigeants des petites entreprises | Fait |
| 4 | Géolocalisation des offres, ligne de commande | Fait |
| 5 | Offres France Travail (fait : 345 offres autour de Mulhouse) + La Bonne Boîte | La Bonne Boîte : accès à faire valider par France Travail |
| 6 | RAG : réécriture, recherche hybride, re-ranking, évaluation | À faire (clé Anthropic + étiquettes) |
| 7 | Écart de compétences + mini-projets, fiche entreprise, brouillon de message | À faire |
| 8 | Application web avec la carte, suivi des candidatures | À faire |
| 9 | Démo en ligne, mesures, ligne de CV | À faire |

Qualité des données repérée sur France Travail : des « alternances » sont publiées par des écoles
qui recrutent des élèves pour leurs formations, pas par des employeurs. À signaler en croisant
avec l'annuaire des entreprises, qui indique les organismes de formation.

À traiter : trouver le site carrières d'une entreprise à partir de l'annuaire (qui ne donne pas
de site web) : Wikidata (données ouvertes, par SIREN), offres France Travail, recherche web.

## Ce que Moundir fait lui-même

Pour pouvoir tout expliquer en entretien : les étiquettes d'évaluation (quelles offres sont
pertinentes pour lui), le tableau de bord, l'analyse de ses résultats, et au moins une
fonctionnalité codée par lui avec accompagnement.

## Décisions

- Dépôt : `Moundirhzr2/job-radar`, privé jusqu'à la démo, un seul collaborateur (Moundir).
  Commits au nom de Moundir, sans ligne de co-auteur.
- Réseau de l'environnement cloud : accès **Full** (lecture des sites d'entreprises).
- Variables d'environnement (jamais dans le chat ni dans le dépôt) :
  `FRANCE_TRAVAIL_CLIENT_ID`, `FRANCE_TRAVAIL_CLIENT_SECRET`, `RADAR_ANTHROPIC_API_KEY`.
- Modèle Claude par défaut : `claude-opus-5`, configurable.
- Le moteur du projet précédent (dépôt `Rag` : recherche hybride pgvector + plein texte, RRF,
  citations, évaluation) est réutilisé.

## Prérequis

- [x] Dépôt `job-radar` créé et relié à la session
- [x] Réseau de l'environnement en accès Full
- [ ] Application francetravail.io avec Offres d'emploi v2 et La Bonne Boîte
- [ ] Clé API Anthropic avec plafond de dépenses
- [ ] Clés enregistrées en variables d'environnement
