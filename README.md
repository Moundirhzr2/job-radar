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

## Ce que fait le radar

- **Les entreprises autour de soi** : tous les employeurs qui ont un établissement dans le rayon
  choisi, depuis l'annuaire officiel des entreprises, avec leur activité et leur taille.
- **Les entreprises qui recrutent sans publier d'offre** : classées par potentiel d'embauche
  pour un métier (France Travail – La Bonne Boîte), avec l'indication des entreprises qui
  acceptent les candidatures spontanées.
- **Les offres France Travail** autour d'une commune, relues régulièrement : les offres retirées
  quittent le radar.
- **Les offres publiées sur les sites carrières des entreprises** : données structurées
  `JobPosting` des pages d'offres, flux publics des logiciels de recrutement (Greenhouse, Lever,
  Recruitee) et sitemaps publiés pour les moteurs de recherche.
- **Les jobs étudiants, selon les heures** : 26 h par semaine au plus. Quand l'offre donne une
  fourchette (« de 24 à 30 heures par semaine ») ou des heures négociables, la valeur la plus
  basse compte. La mention « temps partiel » ne suffit pas : France Travail l'applique à un
  contrat de 34 h 12.
- **La recherche en langage naturel** (« alternance data analyst près de Mulhouse ») : un RAG
  mesuré, décrit plus bas.
- **Mon profil face à une offre** : ce que j'ai déjà, ce qui est à confirmer, ce qui me manque et
  un mini-projet pour l'apprendre ; puis un brouillon de message à la personne qui recrute.

```bash
docker compose up -d --wait        # PostgreSQL 17 + PostGIS + pgvector
uv sync

uv run radar companies --town Mulhouse --radius 15 --sections J --naf 62,63  # employeurs du numérique
uv run radar hiring --town Mulhouse --radius 30 --field data      # qui recrute, même sans offre
uv run radar francetravail --town Mulhouse --radius 30 --kind apprenticeship student_job
uv run radar francetravail --refresh                             # relit les offres connues
uv run radar careers https://groupeoci.teamtailor.com/jobs       # offres d'un site carrières
uv run radar index                                               # vecteurs des offres nouvelles
uv run radar search "alternance data analyst" --town Mulhouse    # la recherche RAG
uv run radar fit 629                                             # mon profil face à l'offre n°629
uv run radar draft 629                                           # brouillon au recruteur
```

Essais sur données réelles autour de Mulhouse : 3 681 offres lues (France Travail et sites
carrières), dont 3 126 encore en ligne (555 retirées par les employeurs en quelques jours),
353 alternances à moins de 100 km et 258 jobs étudiants ; 172 employeurs du numérique à moins
de 15 km, dont 6 que La Bonne Boîte signale comme susceptibles de recruter.

Ce que ces données montrent (début octobre 2026) : à moins de 100 km de Mulhouse, France Travail
ne publie aucune alternance data, et ses 5 alternances en informatique sont toutes déposées par
des écoles. Un
étudiant qui ne cherche que là ne trouve presque rien : c'est pour cela que le radar lit aussi
les sites carrières et repère les entreprises qui recrutent sans publier d'offre.

## La recherche : un RAG qui se mesure

Une demande passe par quatre étapes, chacune pouvant être désactivée pour mesurer ce qu'elle
apporte :

1. **Réécriture** : Claude (Opus 5.5, sortie structurée validée par Pydantic) transforme la
   demande et mon profil en une phrase qui décrit l'offre idéale, des mots-clés exacts en
   français et en anglais, et les types de contrat.
2. **Recherche hybride dans le rayon** : PostGIS garde les offres à moins de X km ; elles sont
   classées par le sens (vecteurs `multilingual-e5-large`, calculés en local) et par les mots
   (plein texte français, sans accents), puis les deux classements sont fusionnés (RRF).
3. **Re-ranking** : un cross-encoder multilingue (`jina-reranker-v2`, en local) relit chaque
   paire demande / offre et réordonne les 50 premières.
4. **Seuil de confiance** : si la meilleure offre a un score inférieur à 0,2, le radar dit qu'il
   n'a rien trouvé de convaincant au lieu de présenter des offres faibles comme des réponses.

### Mesurer avant de faire confiance

- **5 demandes réelles**, les miennes : alternance data (30 km), alternance en informatique
  (100 km), emploi data (30 km), poste en informatique (30 km), job étudiant le week-end (15 km).
- **Mise en commun** (pooling) : les 10 premières offres de chacune des 6 configurations forment
  les offres à juger, 129 offres jugées au total.
- **Jugement en aveugle** sur une page dédiée : les offres sont mélangées, sans savoir quelle
  méthode les a trouvées. J'en ai jugé 72 moi-même. Claude en a jugé 57
  avec mes règles écrites (« data » = bases de données, data analyst, data engineer, BI, big
  data ; job étudiant = 26 h par semaine au plus) : les offres que seule une nouvelle
  configuration avait trouvées, et 5 « pertinente » clairement hors sujet qu'il a corrigés.
  Chacun de ses avis porte sa raison, visible sur la page, et je garde le dernier mot ;
  `eval/labels.json` garde qui a jugé quoi. Les offres retirées depuis ne comptent plus : aucune
  méthode ne peut les trouver.
- **Mesures** : nDCG@10 (les offres pertinentes sont-elles en haut ?), MRR@10 (à quel rang
  arrive la première ?), P@10 (part d'offres pertinentes dans les 10 premières), rappel@50.

| Configuration | nDCG@10 | MRR@10 | P@10 | Rappel@50 |
|---|---:|---:|---:|---:|
| mots-clés seuls | 0,00 | 0,00 | 0,00 | 0,00 |
| vecteurs seuls | 0,56 | 0,45 | 0,30 | 0,99 |
| hybride | 0,49 | 0,42 | 0,28 | 0,99 |
| hybride + re-ranking | 0,51 | 0,46 | 0,23 | 0,99 |
| réécriture + hybride | 0,64 | 0,57 | 0,30 | 0,96 |
| **réécriture + hybride + re-ranking** | **0,73** | **0,69** | 0,28 | 0,96 |

Moyennes sur les 4 demandes qui ont des offres pertinentes ; mesure du 8 octobre 2026.

Ce que le tableau montre :

- **La réécriture apporte le plus** (hybride : 0,49 → 0,64), et **le re-ranking ne paie qu'après
  elle** : seul, il ne change presque rien (0,51) ; sur la demande réécrite, il monte à
  **0,73**, et la première offre pertinente arrive le plus souvent en tête (MRR 0,69).
- **Le problème est le rang, pas la présence** : avec les vecteurs, la bonne offre est presque
  toujours dans les 50 premières (rappel@50 ≥ 0,96). C'est l'ordre qu'il faut corriger, et
  c'est le rôle du re-ranking.
- **Les mots-clés seuls ne trouvent rien** : sans réécriture, la demande entière sert de
  requête, et aucune offre ne contient tous ses mots. Les mots exacts ne servent que s'ils sont
  bien choisis, ce que fait la réécriture.
- **La réécriture a ses pièges, et la mesure les montre** : « réseaux » a fait remonter un poste
  de manœuvre sur un chantier de réseaux secs, et le filtre « alternance », faute d'alternance
  data, des alternances d'échafaudeur ou de commis de bar.
- **P@10 reste bas (0,28) parce que le marché est maigre** : sur les 4 demandes, trois n'ont
  qu'une seule offre pertinente en ligne autour de Mulhouse (alternance en informatique, emploi
  data, poste en informatique). La recherche ne peut pas trouver ce qui n'est pas publié : d'où
  les entreprises qui recrutent sans offre.

**Le seuil de confiance vient des mêmes jugements** (`radar eval score` le recalcule) : pour
chaque demande qui a des offres pertinentes, la meilleure offre obtient au moins 0,23 ; pour
l'alternance data à 30 km, où rien ne correspond, jamais plus de 0,17. Au seuil de 0,2, le radar
répond aux quatre premières et dit honnêtement qu'il n'a rien de convaincant pour la cinquième
(un seuil de 0,5 aurait caché presque toutes les bonnes offres). Une offre pertinente peut
tomber sous le seuil, plus bas dans la liste : elle reste affichée, à part, parmi les « moins
convaincantes ».

**Limites** : 5 demandes et 20 offres pertinentes, c'est un petit jeu d'évaluation, qui mesure
une photo de la base du 8 octobre 2026. Il sert à comparer les configurations entre elles, pas à
promettre un score.

## Mon profil face à une offre

`radar fit 629` compare l'offre à mon profil (`data/profile.md`, mon CV en texte, jamais publié
dans le dépôt) et range chaque exigence de l'offre :

- **ce que j'ai déjà**, avec la phrase de mon profil qui le prouve ;
- **à confirmer** : une expérience proche ; l'outil propose une ligne de CV à ajouter
  *seulement si c'est vrai* ;
- **ce qui me manque**, avec pour les plus importants un mini-projet d'un week-end, sur des
  données publiques, qui laisse quelque chose à montrer (dépôt GitHub, notebook, tableau de
  bord).

`radar draft 629` écrit ensuite un court message pour la personne dont le contact est publié dans
l'offre. Je le relis et je l'envoie moi-même : rien n'est envoyé automatiquement.

Rien n'est pris sur parole : chaque citation (de l'offre comme de mon profil) est recherchée mot
pour mot. Une compétence « acquise » dont la citation est introuvable dans mon profil redevient
« à confirmer », et un fait du message qui n'est pas dans mon profil est signalé avant l'envoi.

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

110 tests : lecture des pages et des flux, règles robots.txt, heures de travail sur des
formulations d'offres réelles, géolocalisation, reprise sur erreur réseau, appels à Claude face à
un faux serveur (sans clé ni réseau), et tests d'intégration sur un vrai PostgreSQL + PostGIS +
pgvector. La CI GitHub Actions les exécute à chaque push.
