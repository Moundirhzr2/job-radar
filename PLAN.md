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

## Feuille de route

| Semaines | Résultat | Statut |
|---|---|---|
| 1–2 | Radar : carte, rayon, offres des API officielles, entreprises autour, chemins de contact | En cours |
| 3 | Lecture des offres sur les pages carrières des entreprises | En cours (lecteur JobPosting et flux) |
| 4 | Écart de compétences + mini-projets, fiche entreprise, brouillon de message | À faire |
| 5 | Suivi + statistiques de réponse, test avec des camarades | À faire |
| 6 | Démo en ligne, README, mesures, ligne de CV | À faire |

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

- [ ] Dépôt `job-radar` créé et relié à la session
- [ ] Réseau de l'environnement en accès Full
- [ ] Application francetravail.io avec Offres d'emploi v2 et La Bonne Boîte
- [ ] Clé API Anthropic avec plafond de dépenses
- [ ] Clés enregistrées en variables d'environnement
