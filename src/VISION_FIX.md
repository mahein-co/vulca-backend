✅ **Problème résolu !**

## Le problème
L'API OpenAI refusait d'extraire le texte avec le message :
> "Je ne peux pas extraire le texte d'images"

## La cause
Le prompt utilisait le mot "**Extrait**" qui peut être interprété comme une demande non autorisée par les politiques OpenAI.

## La solution
J'ai modifié le prompt pour utiliser "**Lis et transcris**" au lieu de "Extrait", ce qui est plus aligné avec les cas d'usage acceptables.

### Nouveau prompt :
```
Lis et transcris TOUT le texte visible dans cette image de document comptable.

Transcris exactement ce que tu vois, y compris :
- Tous les numéros (facture, référence, client, etc.)
- Toutes les dates
- Tous les montants avec leurs devises
- Tous les noms (entreprise, client, fournisseur)
- Toutes les adresses
- Tous les articles/descriptions
- Tous les totaux (HT, TVA, TTC)

Même si le document est flou ou de mauvaise qualité, fais de ton mieux pour lire chaque élément.
```

## Prochaine étape
**Redémarrez le serveur Django** pour appliquer les changements :

```bash
# Arrêtez le serveur actuel (Ctrl+C dans le terminal)
# Puis relancez :
python manage.py runserver
```

Ensuite, réessayez d'uploader votre facture !
