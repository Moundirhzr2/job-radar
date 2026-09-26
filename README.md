# Job Radar

**Moins de candidatures, envoyées aux bonnes personnes.**

Job Radar montre aux étudiants ce qui recrute autour d'eux (stages, alternances, emplois, jobs
étudiants), y compris les entreprises qui n'ont rien publié, et le chemin le plus direct vers la
personne qui décide. Uniquement à partir de sources officielles et de données que les
entreprises publient elles-mêmes.

## Pourquoi ce projet

Je suis étudiant en Master 1 Informatique et Mobilité à l'Université de Haute-Alsace, et je
cherche une alternance. Comme beaucoup d'étudiants, je me suis souvent retrouvé bloqué au moment
de postuler : des candidatures envoyées sur de grands sites d'emploi, sans savoir qui les lirait,
et le plus souvent sans réponse.

Ce projet représente ma situation actuelle. J'ai pris le temps d'y réfléchir et je me suis dit :
pourquoi ne pas construire l'outil qui m'aiderait, moi, dans la vraie vie ? Un outil qui me
montre ce qui recrute autour de moi, y compris les entreprises qui ne publient pas d'offres, et
qui m'indique à qui m'adresser pour arriver jusqu'à la personne qui recrute.

Je l'utilise pour ma propre recherche. S'il m'aide, il pourra aider d'autres étudiants dans la
même situation.

## Ce qui fonctionne aujourd'hui

- **Les entreprises autour de soi** : tous les employeurs qui ont un établissement dans le rayon
  choisi, depuis l'annuaire officiel des entreprises, avec leur activité et leur taille.
- **Les offres publiées sur les sites carrières des entreprises** : lecture des données
  structurées `JobPosting` des pages d'offres, des flux publics des logiciels de recrutement
  (Greenhouse, Lever, Recruitee) et des sitemaps que les sites publient pour les moteurs de
  recherche.
- **Le radar** : les offres à moins de X km, triées par distance, filtrées par type (stage,
  alternance, emploi, job étudiant). PostgreSQL + PostGIS.

```bash
docker compose up -d --wait        # PostgreSQL 17 + PostGIS + pgvector
uv sync

uv run radar companies --town Mulhouse --radius 15 --naf 62,63   # employeurs du numérique
uv run radar careers https://groupeoci.teamtailor.com/jobs       # offres d'un site carrières
uv run radar feed recruitee amiparis                             # flux d'un logiciel de recrutement
uv run radar offers --town Mulhouse --radius 30 --kind internship apprenticeship
```

Premier essai sur données réelles : 64 offres lues sur les sites de trois entreprises, placées
sur la carte et retrouvées autour de Mulhouse et de Paris.

## Les règles

Le projet ne fait que ce qui est légal et respectueux des sites et des personnes :

1. Uniquement des API officielles et des données publiées par les entreprises pour être lues
   par des machines.
2. Aucun scraping de sites d'emploi ni de LinkedIn, aucune collecte d'adresses e-mail
   personnelles.
3. Chaque site est lu selon ses règles : `robots.txt` respecté (SmartRecruiters est exclu pour
   cette raison), délai entre deux requêtes, et un site qui refuse l'usage de son contenu par une
   IA (`Content-Signal: ai-input=no`) n'est jamais envoyé à un modèle.
4. Données personnelles réduites au minimum : pour les petites entreprises, le nom et la fonction
   du dirigeant (registre public), jamais sa date de naissance.
5. Jamais d'invention sur un CV, pas de candidature automatique : l'outil mène à la bonne page
   et prépare, l'étudiant envoie.

Le détail du produit, des sources, de la partie RAG et de la feuille de route est dans
[PLAN.md](PLAN.md).

## Tests

```bash
docker compose up -d --wait && uv run pytest
```

Tests unitaires (lecture des pages et des flux, règles robots.txt, géolocalisation, reprise sur
erreur réseau) et tests d'intégration sur un vrai PostgreSQL + PostGIS + pgvector. La CI GitHub
Actions les exécute à chaque push.
