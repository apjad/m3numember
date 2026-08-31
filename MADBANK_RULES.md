# Madbank — regler til AI-genererede retter

Kopiér denne fil ind i en chat med ChatGPT, Grok eller en anden AI. Skriv hvilken slags mad
du gerne vil have forslag til (fx "hverdagsmad med kylling", eller læg et billede af en
tilbudsavis ved og bed om forslag ud fra det). Bed AI'en generere nye retter efter reglerne
herunder, og aflever kun JSON'en tilbage — den skal bare smides ind i `tilfoej-fra-ai.sh`.

## Format

Output skal være **præcis ét JSON-array**, intet andet (ingen forklaring, ingen markdown-kodeblok udenom):

```json
[
  {
    "name": "Kort retnavn",
    "recipeURL": "",
    "items": ["ingrediens 1", "ingrediens 2", "..."]
  }
]
```

## Regler

1. **`name`**: kort og præcist, **maks 2-4 ord**. Ikke en hel sætning som "Pitabrød med hakket
   oksekød, salat og dressing" — det skal kunne stå på et lille kort i en app uden at fylde
   det hele. Hellere "Pitabrød med oksekød" end den lange variant.
2. **`recipeURL`**: sæt til tom streng `""` medmindre du er blevet givet et konkret link.
3. **`items`**: hold det generisk (hovedingredienser, ikke mængder/gram — det er ikke en
   opskrift med præcise mål, bare hvad der skal på indkøbslisten). Ingen tilberedningsnotater.
4. **Ingen dubletter** — generér ikke en ret der allerede findes på den nuværende liste (se
   nedenfor, eller vedhæft en frisk liste fra `hent-liste.sh`). Er en ny ret meget lig en
   eksisterende (samme ret, bare +1 ingrediens), så drop den — det er ikke en ny ret.
5. Hold det **realistisk dansk hverdagsmad** medmindre andet er bedt om.
6. Generér typisk **5-15 retter** ad gangen, ikke flere — nemmere at gennemse før de tilføjes.

## Nuværende liste (undgå dubletter af disse)

Kør `./hent-liste.sh` for den friske liste — den ændrer sig løbende.Øjebliksbillede fra denne fil blev sidst opdateret manuelt, stol ikke blindt på den:

- Frikadeller, Lasagne, Kylling i karry, Boller i karry, Burgere, Stegt flæsk, Taco, Pizza,
  Spaghetti bolognese, Pitabrød med oksekød, Fiskefrikadeller, Tortilla med oksekød,
  Hjemmelavet pizza, Pasta med bacon og fløde, Frikadeller m. pasta, Hotdogs,
  Pasta med kødboller, Toast med skinke, Nachos, Pølser med pasta, Makaroni med kødsovs,
  Kylling i tortilla, Fiskefileter, Pasta med skinke, Pitapizza, Pasta med bacon og tomat,
  Rugbrød med fiskefrikadeller, Tortillapizza, Pølser med ris, Kylling Nuggets

## Sådan bruges resultatet

1. Kopiér AI'ens JSON-svar.
2. Kør `./tilfoej-fra-ai.sh` i denne mappe, indsæt JSON'en når den beder om det, afslut med
   `Ctrl-D` på en tom linje.
3. Scriptet dropper automatisk alle retter hvis navn allerede findes på listen, viser dig hvad
   der reelt bliver tilføjet, og beder om bekræftelse før det committer og pusher.
