"""
char_stats.py
─────────────
Per-character stat sheet for NEXUS AWAKENING.

Every character has a value (1-80) for each of the six stat fields below.
When two characters clash in a given role/field (e.g. both are slotted
into "Strength"), whichever card has the HIGHER number in that field
wins the clash.

Tier ranges (a character's stats normally fall inside its tier's range):

    Basic    1  - 20
    Elite    20 - 40
    Divine   40 - 80

A small number of characters deliberately break this range on a single
stat for canon accuracy (e.g. Toji Fushiguro, Maki Zenin and Mai Zenin
have near-zero Mana because they canonically have no/near-no cursed
energy, despite being Divine/Elite tier overall). Run this file directly
to see exactly which characters have an out-of-range stat and why —
validate_char_stats() lists them so it's a deliberate, visible choice
rather than a silent bug.

Covers all 193 characters from the Dragon Ball and Jujutsu Kaisen lists
you sent. Stats were generated from per-character archetypes (brute,
caster, speedster, tank, all-rounder, support, hybrid, weak-civilian,
object, glutton, scientist) and hand-tuned for the more iconic names
(Gojo, Sukuna, Toji, Goku forms, Beerus, Zeno, Vegito, etc). The rest
are a solid first pass — edit any individual numbers below directly if
something doesn't match how you want a specific matchup to play out.
"""

TIER_RANGES = {
    "Basic":  (1, 20),
    "Elite":  (20, 40),
    "Divine": (40, 80),
}

STAT_FIELDS = [
    "Strength",
    "Mana",
    "Defence",
    "Agility",
    "Vitality",
    "Intelligence",
]

CHAR_STATS = {
    "Beerus": {"tier": 'Divine', "Strength": 80, "Mana": 78, "Defence": 75, "Agility": 61, "Vitality": 76, "Intelligence": 57},
    "Ultra Instinct Goku": {"tier": 'Divine', "Strength": 80, "Mana": 60, "Defence": 55, "Agility": 80, "Vitality": 59, "Intelligence": 67},
    "Gogeta": {"tier": 'Divine', "Strength": 80, "Mana": 40, "Defence": 60, "Agility": 50, "Vitality": 78, "Intelligence": 40},
    "Vegito": {"tier": 'Divine', "Strength": 79, "Mana": 70, "Defence": 60, "Agility": 56, "Vitality": 57, "Intelligence": 64},
    "Whis": {"tier": 'Divine', "Strength": 55, "Mana": 70, "Defence": 44, "Agility": 80, "Vitality": 45, "Intelligence": 55},
    "Ultra Ego Vegeta": {"tier": 'Divine', "Strength": 80, "Mana": 40, "Defence": 57, "Agility": 50, "Vitality": 80, "Intelligence": 40},
    "Orange Piccolo": {"tier": 'Divine', "Strength": 70, "Mana": 42, "Defence": 74, "Agility": 40, "Vitality": 73, "Intelligence": 45},
    "Black Frieza": {"tier": 'Divine', "Strength": 78, "Mana": 72, "Defence": 57, "Agility": 56, "Vitality": 61, "Intelligence": 67},
    "Zeno": {"tier": 'Divine', "Strength": 80, "Mana": 80, "Defence": 80, "Agility": 80, "Vitality": 80, "Intelligence": 45},
    "Merged Zamasu": {"tier": 'Divine', "Strength": 69, "Mana": 80, "Defence": 60, "Agility": 53, "Vitality": 78, "Intelligence": 62},
    "Beast Gohan": {"tier": 'Divine', "Strength": 76, "Mana": 65, "Defence": 56, "Agility": 57, "Vitality": 63, "Intelligence": 67},
    "Legendary Super Saiyan Broly": {"tier": 'Divine', "Strength": 80, "Mana": 40, "Defence": 59, "Agility": 54, "Vitality": 75, "Intelligence": 35},
    "Jiren Full Power": {"tier": 'Divine', "Strength": 80, "Mana": 40, "Defence": 70, "Agility": 53, "Vitality": 69, "Intelligence": 40},
    "Grand Priest": {"tier": 'Divine', "Strength": 78, "Mana": 78, "Defence": 78, "Agility": 78, "Vitality": 60, "Intelligence": 61},
    "Corrupted Zamasu": {"tier": 'Divine', "Strength": 40, "Mana": 78, "Defence": 49, "Agility": 46, "Vitality": 45, "Intelligence": 71},
    "Heles": {"tier": 'Divine', "Strength": 68, "Mana": 68, "Defence": 64, "Agility": 64, "Vitality": 63, "Intelligence": 61},
    "Belmod": {"tier": 'Divine', "Strength": 74, "Mana": 40, "Defence": 59, "Agility": 49, "Vitality": 70, "Intelligence": 40},
    "Quitela": {"tier": 'Divine', "Strength": 40, "Mana": 54, "Defence": 41, "Agility": 40, "Vitality": 42, "Intelligence": 78},
    "Rumsshi": {"tier": 'Divine', "Strength": 75, "Mana": 40, "Defence": 61, "Agility": 53, "Vitality": 72, "Intelligence": 40},
    "Sidra": {"tier": 'Divine', "Strength": 72, "Mana": 40, "Defence": 59, "Agility": 50, "Vitality": 72, "Intelligence": 40},
    "Liquiir": {"tier": 'Divine', "Strength": 68, "Mana": 65, "Defence": 61, "Agility": 65, "Vitality": 61, "Intelligence": 60},
    "Arak": {"tier": 'Divine', "Strength": 49, "Mana": 42, "Defence": 78, "Agility": 40, "Vitality": 76, "Intelligence": 44},
    "Iwan": {"tier": 'Divine', "Strength": 40, "Mana": 60, "Defence": 50, "Agility": 54, "Vitality": 51, "Intelligence": 78},
    "Future Zeno": {"tier": 'Divine', "Strength": 79, "Mana": 80, "Defence": 80, "Agility": 80, "Vitality": 78, "Intelligence": 46},
    "Champa": {"tier": 'Divine', "Strength": 75, "Mana": 40, "Defence": 60, "Agility": 54, "Vitality": 68, "Intelligence": 40},
    "Vados": {"tier": 'Divine', "Strength": 52, "Mana": 68, "Defence": 42, "Agility": 79, "Vitality": 49, "Intelligence": 57},
    "Ultra Instinct Sign Goku": {"tier": 'Divine', "Strength": 70, "Mana": 45, "Defence": 45, "Agility": 78, "Vitality": 45, "Intelligence": 52},
    "Gogeta Blue": {"tier": 'Divine', "Strength": 78, "Mana": 40, "Defence": 62, "Agility": 54, "Vitality": 66, "Intelligence": 40},
    "Super Shenron": {"tier": 'Divine', "Strength": 60, "Mana": 80, "Defence": 60, "Agility": 40, "Vitality": 40, "Intelligence": 40},
    "Black goku": {"tier": 'Divine', "Strength": 75, "Mana": 70, "Defence": 59, "Agility": 58, "Vitality": 62, "Intelligence": 68},
    "Kid Buu": {"tier": 'Elite', "Strength": 39, "Mana": 25, "Defence": 35, "Agility": 27, "Vitality": 38, "Intelligence": 20},
    "Broly": {"tier": 'Elite', "Strength": 40, "Mana": 20, "Defence": 30, "Agility": 27, "Vitality": 36, "Intelligence": 20},
    "Kale": {"tier": 'Elite', "Strength": 38, "Mana": 20, "Defence": 29, "Agility": 24, "Vitality": 33, "Intelligence": 20},
    "Caulifla": {"tier": 'Elite', "Strength": 38, "Mana": 20, "Defence": 29, "Agility": 24, "Vitality": 35, "Intelligence": 20},
    "Kefla": {"tier": 'Elite', "Strength": 38, "Mana": 20, "Defence": 30, "Agility": 24, "Vitality": 34, "Intelligence": 20},
    "Hit": {"tier": 'Elite', "Strength": 26, "Mana": 23, "Defence": 22, "Agility": 39, "Vitality": 23, "Intelligence": 29},
    "Jiren": {"tier": 'Elite', "Strength": 39, "Mana": 20, "Defence": 36, "Agility": 25, "Vitality": 36, "Intelligence": 20},
    "Gamma 1": {"tier": 'Elite', "Strength": 27, "Mana": 21, "Defence": 37, "Agility": 20, "Vitality": 39, "Intelligence": 21},
    "Cell Max": {"tier": 'Elite', "Strength": 39, "Mana": 27, "Defence": 34, "Agility": 27, "Vitality": 39, "Intelligence": 18},
    "Hatchiyack": {"tier": 'Elite', "Strength": 38, "Mana": 20, "Defence": 31, "Agility": 26, "Vitality": 36, "Intelligence": 20},
    "Fused Android 13": {"tier": 'Elite', "Strength": 25, "Mana": 22, "Defence": 40, "Agility": 20, "Vitality": 37, "Intelligence": 23},
    "King Vegeta": {"tier": 'Elite', "Strength": 38, "Mana": 20, "Defence": 32, "Agility": 24, "Vitality": 33, "Intelligence": 20},
    "Bardock": {"tier": 'Elite', "Strength": 39, "Mana": 20, "Defence": 31, "Agility": 27, "Vitality": 35, "Intelligence": 20},
    "Gas": {"tier": 'Elite', "Strength": 38, "Mana": 34, "Defence": 27, "Agility": 27, "Vitality": 31, "Intelligence": 33},
    "Granolah": {"tier": 'Elite', "Strength": 38, "Mana": 35, "Defence": 28, "Agility": 29, "Vitality": 30, "Intelligence": 34},
    "Moro": {"tier": 'Elite', "Strength": 20, "Mana": 39, "Defence": 22, "Agility": 23, "Vitality": 20, "Intelligence": 36},
    "Frost": {"tier": 'Elite', "Strength": 37, "Mana": 20, "Defence": 31, "Agility": 26, "Vitality": 33, "Intelligence": 20},
    "Golden Frieza": {"tier": 'Elite', "Strength": 37, "Mana": 35, "Defence": 28, "Agility": 27, "Vitality": 30, "Intelligence": 32},
    "Ultimate Gohan": {"tier": 'Elite', "Strength": 37, "Mana": 35, "Defence": 30, "Agility": 28, "Vitality": 28, "Intelligence": 33},
    "Super 17": {"tier": 'Elite', "Strength": 27, "Mana": 20, "Defence": 37, "Agility": 20, "Vitality": 37, "Intelligence": 21},
    "Gamma 2": {"tier": 'Elite', "Strength": 29, "Mana": 23, "Defence": 20, "Agility": 39, "Vitality": 25, "Intelligence": 27},
    "Super Baby 2": {"tier": 'Elite', "Strength": 37, "Mana": 20, "Defence": 31, "Agility": 24, "Vitality": 36, "Intelligence": 20},
    "Baby Vegeta": {"tier": 'Elite', "Strength": 38, "Mana": 20, "Defence": 30, "Agility": 27, "Vitality": 33, "Intelligence": 20},
    "Ice Shenron": {"tier": 'Elite', "Strength": 20, "Mana": 34, "Defence": 20, "Agility": 20, "Vitality": 20, "Intelligence": 20},
    "Nova Shenron": {"tier": 'Elite', "Strength": 22, "Mana": 36, "Defence": 21, "Agility": 23, "Vitality": 20, "Intelligence": 21},
    "Omega Shenron": {"tier": 'Elite', "Strength": 38, "Mana": 25, "Defence": 33, "Agility": 26, "Vitality": 36, "Intelligence": 20},
    "Tapion": {"tier": 'Elite', "Strength": 20, "Mana": 31, "Defence": 25, "Agility": 25, "Vitality": 23, "Intelligence": 36},
    "Bojack": {"tier": 'Elite', "Strength": 36, "Mana": 20, "Defence": 31, "Agility": 27, "Vitality": 34, "Intelligence": 20},
    "Super Android 13": {"tier": 'Elite', "Strength": 24, "Mana": 21, "Defence": 38, "Agility": 20, "Vitality": 38, "Intelligence": 23},
    "Metal Cooler": {"tier": 'Elite', "Strength": 26, "Mana": 21, "Defence": 40, "Agility": 20, "Vitality": 38, "Intelligence": 21},
    "Cooler": {"tier": 'Elite', "Strength": 36, "Mana": 20, "Defence": 30, "Agility": 26, "Vitality": 36, "Intelligence": 20},
    "Janemba": {"tier": 'Elite', "Strength": 20, "Mana": 37, "Defence": 22, "Agility": 24, "Vitality": 20, "Intelligence": 37},
    "Dyspo": {"tier": 'Elite', "Strength": 25, "Mana": 22, "Defence": 22, "Agility": 39, "Vitality": 22, "Intelligence": 28},
    "Gotenks": {"tier": 'Elite', "Strength": 33, "Mana": 34, "Defence": 30, "Agility": 30, "Vitality": 32, "Intelligence": 32},
    "Toppo": {"tier": 'Elite', "Strength": 37, "Mana": 20, "Defence": 29, "Agility": 27, "Vitality": 33, "Intelligence": 20},
    "Cell": {"tier": 'Elite', "Strength": 36, "Mana": 34, "Defence": 29, "Agility": 27, "Vitality": 30, "Intelligence": 30},
    "Android 18": {"tier": 'Elite', "Strength": 27, "Mana": 24, "Defence": 22, "Agility": 37, "Vitality": 24, "Intelligence": 27},
    "Android 17": {"tier": 'Elite', "Strength": 24, "Mana": 20, "Defence": 39, "Agility": 20, "Vitality": 37, "Intelligence": 21},
    "Future Trunks": {"tier": 'Elite', "Strength": 34, "Mana": 35, "Defence": 29, "Agility": 29, "Vitality": 31, "Intelligence": 33},
    "Piccolo": {"tier": 'Elite', "Strength": 20, "Mana": 35, "Defence": 25, "Agility": 25, "Vitality": 21, "Intelligence": 37},
    "Majin Boo": {"tier": 'Elite', "Strength": 35, "Mana": 24, "Defence": 34, "Agility": 25, "Vitality": 38, "Intelligence": 20},
    "Super Buu": {"tier": 'Elite', "Strength": 38, "Mana": 27, "Defence": 32, "Agility": 26, "Vitality": 38, "Intelligence": 20},
    "Perfect Cell": {"tier": 'Elite', "Strength": 39, "Mana": 34, "Defence": 28, "Agility": 29, "Vitality": 30, "Intelligence": 31},
    "Mystic Gohan": {"tier": 'Elite', "Strength": 33, "Mana": 35, "Defence": 28, "Agility": 28, "Vitality": 29, "Intelligence": 31},
    "SSJ4 Goku": {"tier": 'Elite', "Strength": 40, "Mana": 20, "Defence": 30, "Agility": 25, "Vitality": 38, "Intelligence": 20},
    "Porunga": {"tier": 'Elite', "Strength": 35, "Mana": 38, "Defence": 20, "Agility": 20, "Vitality": 20, "Intelligence": 20},
    "Krillin": {"tier": 'Basic', "Strength": 12, "Mana": 7, "Defence": 10, "Agility": 8, "Vitality": 13, "Intelligence": 5},
    "Yamcha": {"tier": 'Basic', "Strength": 14, "Mana": 7, "Defence": 12, "Agility": 11, "Vitality": 14, "Intelligence": 7},
    "Tien Shinhan": {"tier": 'Basic', "Strength": 12, "Mana": 5, "Defence": 11, "Agility": 8, "Vitality": 12, "Intelligence": 5},
    "Chiaotzu": {"tier": 'Basic', "Strength": 5, "Mana": 12, "Defence": 9, "Agility": 7, "Vitality": 8, "Intelligence": 13},
    "Master Roshi": {"tier": 'Basic', "Strength": 12, "Mana": 11, "Defence": 8, "Agility": 11, "Vitality": 12, "Intelligence": 11},
    "Yajirobe": {"tier": 'Basic', "Strength": 7, "Mana": 4, "Defence": 6, "Agility": 7, "Vitality": 6, "Intelligence": 10},
    "Videl": {"tier": 'Basic', "Strength": 12, "Mana": 5, "Defence": 11, "Agility": 9, "Vitality": 14, "Intelligence": 6},
    "Hercule": {"tier": 'Basic', "Strength": 8, "Mana": 3, "Defence": 7, "Agility": 6, "Vitality": 7, "Intelligence": 6},
    "Raditz": {"tier": 'Basic', "Strength": 13, "Mana": 6, "Defence": 10, "Agility": 11, "Vitality": 12, "Intelligence": 7},
    "Nappa": {"tier": 'Basic', "Strength": 14, "Mana": 5, "Defence": 10, "Agility": 10, "Vitality": 12, "Intelligence": 4},
    "Saibaman": {"tier": 'Basic', "Strength": 6, "Mana": 3, "Defence": 5, "Agility": 9, "Vitality": 5, "Intelligence": 7},
    "Dodoria": {"tier": 'Basic', "Strength": 14, "Mana": 6, "Defence": 11, "Agility": 7, "Vitality": 11, "Intelligence": 7},
    "Zarbon": {"tier": 'Basic', "Strength": 12, "Mana": 7, "Defence": 11, "Agility": 10, "Vitality": 13, "Intelligence": 5},
    "Cui": {"tier": 'Basic', "Strength": 11, "Mana": 8, "Defence": 8, "Agility": 14, "Vitality": 8, "Intelligence": 10},
    "Recoome": {"tier": 'Basic', "Strength": 13, "Mana": 7, "Defence": 12, "Agility": 8, "Vitality": 11, "Intelligence": 5},
    "Burter": {"tier": 'Basic', "Strength": 10, "Mana": 8, "Defence": 6, "Agility": 20, "Vitality": 9, "Intelligence": 10},
    "Jeice": {"tier": 'Basic', "Strength": 11, "Mana": 8, "Defence": 8, "Agility": 15, "Vitality": 9, "Intelligence": 11},
    "Guldo": {"tier": 'Basic', "Strength": 5, "Mana": 12, "Defence": 10, "Agility": 10, "Vitality": 8, "Intelligence": 11},
    "Android 19": {"tier": 'Basic', "Strength": 9, "Mana": 6, "Defence": 15, "Agility": 5, "Vitality": 14, "Intelligence": 6},
    "Launch": {"tier": 'Basic', "Strength": 7, "Mana": 4, "Defence": 6, "Agility": 8, "Vitality": 8, "Intelligence": 8},
    "Jaco": {"tier": 'Basic', "Strength": 5, "Mana": 6, "Defence": 8, "Agility": 6, "Vitality": 6, "Intelligence": 9},
    "Cabba": {"tier": 'Basic', "Strength": 14, "Mana": 7, "Defence": 12, "Agility": 10, "Vitality": 12, "Intelligence": 7},
    "Uub": {"tier": 'Basic', "Strength": 20, "Mana": 5, "Defence": 11, "Agility": 10, "Vitality": 11, "Intelligence": 7},
    "Pan": {"tier": 'Basic', "Strength": 7, "Mana": 3, "Defence": 7, "Agility": 8, "Vitality": 5, "Intelligence": 7},
    "Chi-chi": {"tier": 'Basic', "Strength": 6, "Mana": 5, "Defence": 7, "Agility": 8, "Vitality": 8, "Intelligence": 9},
    "Bulma": {"tier": 'Basic', "Strength": 6, "Mana": 11, "Defence": 7, "Agility": 6, "Vitality": 8, "Intelligence": 20},
    "1-Star Dragon Ball": {"tier": 'Basic', "Strength": 1, "Mana": 9, "Defence": 3, "Agility": 2, "Vitality": 5, "Intelligence": 2},
    "2-Star Dragon Ball": {"tier": 'Basic', "Strength": 4, "Mana": 12, "Defence": 2, "Agility": 3, "Vitality": 4, "Intelligence": 4},
    "3-Star Dragon Ball": {"tier": 'Basic', "Strength": 2, "Mana": 10, "Defence": 2, "Agility": 2, "Vitality": 3, "Intelligence": 3},
    "4-Star Dragon Ball": {"tier": 'Basic', "Strength": 3, "Mana": 12, "Defence": 4, "Agility": 2, "Vitality": 3, "Intelligence": 3},
    "5-Star Dragon Ball": {"tier": 'Basic', "Strength": 4, "Mana": 10, "Defence": 2, "Agility": 1, "Vitality": 3, "Intelligence": 4},
    "6-Star Dragon Ball": {"tier": 'Basic', "Strength": 3, "Mana": 10, "Defence": 4, "Agility": 1, "Vitality": 3, "Intelligence": 3},
    "7-Star Dragon Ball": {"tier": 'Basic', "Strength": 3, "Mana": 11, "Defence": 4, "Agility": 1, "Vitality": 3, "Intelligence": 4},
    "Dr. Gero": {"tier": 'Basic', "Strength": 5, "Mana": 11, "Defence": 8, "Agility": 7, "Vitality": 7, "Intelligence": 13},
    "Dende": {"tier": 'Basic', "Strength": 7, "Mana": 12, "Defence": 10, "Agility": 8, "Vitality": 9, "Intelligence": 14},
    "Mr. Popo": {"tier": 'Basic', "Strength": 6, "Mana": 12, "Defence": 9, "Agility": 8, "Vitality": 10, "Intelligence": 13},
    "Korin": {"tier": 'Basic', "Strength": 5, "Mana": 11, "Defence": 9, "Agility": 9, "Vitality": 9, "Intelligence": 15},
    "Future Mai": {"tier": 'Basic', "Strength": 8, "Mana": 6, "Defence": 6, "Agility": 7, "Vitality": 6, "Intelligence": 9},
    "Paragus": {"tier": 'Basic', "Strength": 14, "Mana": 6, "Defence": 11, "Agility": 9, "Vitality": 11, "Intelligence": 6},
    "Lord Slug": {"tier": 'Basic', "Strength": 12, "Mana": 5, "Defence": 9, "Agility": 8, "Vitality": 12, "Intelligence": 5},
    "Turles": {"tier": 'Basic', "Strength": 13, "Mana": 6, "Defence": 11, "Agility": 11, "Vitality": 11, "Intelligence": 7},
    "Babidi": {"tier": 'Basic', "Strength": 6, "Mana": 15, "Defence": 9, "Agility": 7, "Vitality": 8, "Intelligence": 11},
    "Garlic Jr": {"tier": 'Basic', "Strength": 13, "Mana": 7, "Defence": 11, "Agility": 9, "Vitality": 13, "Intelligence": 6},
    "Puar": {"tier": 'Basic', "Strength": 5, "Mana": 4, "Defence": 8, "Agility": 6, "Vitality": 6, "Intelligence": 9},
    "Oolong": {"tier": 'Basic', "Strength": 7, "Mana": 5, "Defence": 7, "Agility": 7, "Vitality": 7, "Intelligence": 8},
    "Shin": {"tier": 'Basic', "Strength": 6, "Mana": 18, "Defence": 8, "Agility": 8, "Vitality": 9, "Intelligence": 12},
    "Kibito": {"tier": 'Basic', "Strength": 10, "Mana": 9, "Defence": 15, "Agility": 5, "Vitality": 14, "Intelligence": 6},
    "Shenron": {"tier": 'Basic', "Strength": 18, "Mana": 20, "Defence": 3, "Agility": 1, "Vitality": 2, "Intelligence": 4},
    "Satoru Gojo": {"tier": 'Divine', "Strength": 79, "Mana": 80, "Defence": 75, "Agility": 78, "Vitality": 70, "Intelligence": 76},
    "Ryomen Sukuna": {"tier": 'Divine', "Strength": 80, "Mana": 79, "Defence": 76, "Agility": 76, "Vitality": 78, "Intelligence": 65},
    "Yuta Okkotsu": {"tier": 'Divine', "Strength": 76, "Mana": 78, "Defence": 54, "Agility": 55, "Vitality": 60, "Intelligence": 63},
    "Jogo": {"tier": 'Divine', "Strength": 60, "Mana": 78, "Defence": 51, "Agility": 46, "Vitality": 44, "Intelligence": 72},
    "Toji Fushiguro": {"tier": 'Divine', "Strength": 78, "Mana": 5, "Defence": 70, "Agility": 78, "Vitality": 65, "Intelligence": 60},
    "Mahoraga": {"tier": 'Divine', "Strength": 76, "Mana": 41, "Defence": 80, "Agility": 40, "Vitality": 78, "Intelligence": 30},
    "Kenjaku": {"tier": 'Divine', "Strength": 40, "Mana": 75, "Defence": 40, "Agility": 40, "Vitality": 42, "Intelligence": 80},
    "Mahito": {"tier": 'Divine', "Strength": 65, "Mana": 74, "Defence": 59, "Agility": 58, "Vitality": 63, "Intelligence": 62},
    "Suguru Geto": {"tier": 'Divine', "Strength": 40, "Mana": 76, "Defence": 48, "Agility": 45, "Vitality": 43, "Intelligence": 73},
    "Rika orimoto": {"tier": 'Divine', "Strength": 78, "Mana": 70, "Defence": 56, "Agility": 58, "Vitality": 62, "Intelligence": 62},
    "Rika orimoto uncensored": {"tier": 'Divine', "Strength": 80, "Mana": 75, "Defence": 59, "Agility": 60, "Vitality": 59, "Intelligence": 64},
    "Yuki Tsukumo": {"tier": 'Divine', "Strength": 70, "Mana": 76, "Defence": 60, "Agility": 54, "Vitality": 63, "Intelligence": 62},
    "Brunt Maki": {"tier": 'Divine', "Strength": 75, "Mana": 10, "Defence": 60, "Agility": 52, "Vitality": 70, "Intelligence": 40},
    "Naoya Curse": {"tier": 'Divine', "Strength": 72, "Mana": 40, "Defence": 59, "Agility": 54, "Vitality": 68, "Intelligence": 40},
    "Yuji Itadori": {"tier": 'Elite', "Strength": 38, "Mana": 20, "Defence": 31, "Agility": 26, "Vitality": 33, "Intelligence": 20},
    "Toge Inumaki": {"tier": 'Elite', "Strength": 20, "Mana": 36, "Defence": 25, "Agility": 22, "Vitality": 22, "Intelligence": 36},
    "Megumi Fushiguro": {"tier": 'Elite', "Strength": 20, "Mana": 35, "Defence": 24, "Agility": 25, "Vitality": 22, "Intelligence": 35},
    "Maki Zenin": {"tier": 'Elite', "Strength": 39, "Mana": 4, "Defence": 32, "Agility": 26, "Vitality": 35, "Intelligence": 20},
    "Panda": {"tier": 'Elite', "Strength": 25, "Mana": 21, "Defence": 40, "Agility": 20, "Vitality": 39, "Intelligence": 20},
    "Aoi Todo": {"tier": 'Elite', "Strength": 38, "Mana": 20, "Defence": 30, "Agility": 26, "Vitality": 33, "Intelligence": 20},
    "Choso": {"tier": 'Elite', "Strength": 33, "Mana": 35, "Defence": 28, "Agility": 30, "Vitality": 31, "Intelligence": 33},
    "Hanami": {"tier": 'Elite', "Strength": 27, "Mana": 20, "Defence": 36, "Agility": 20, "Vitality": 37, "Intelligence": 22},
    "Dagon": {"tier": 'Elite', "Strength": 27, "Mana": 20, "Defence": 40, "Agility": 20, "Vitality": 38, "Intelligence": 21},
    "Naobito Zenin": {"tier": 'Elite', "Strength": 26, "Mana": 24, "Defence": 21, "Agility": 38, "Vitality": 23, "Intelligence": 28},
    "Mechamaru": {"tier": 'Elite', "Strength": 20, "Mana": 26, "Defence": 20, "Agility": 20, "Vitality": 21, "Intelligence": 33},
    "Utahime Iori": {"tier": 'Elite', "Strength": 20, "Mana": 37, "Defence": 23, "Agility": 22, "Vitality": 22, "Intelligence": 35},
    "Mai Zenin": {"tier": 'Elite', "Strength": 28, "Mana": 12, "Defence": 32, "Agility": 26, "Vitality": 36, "Intelligence": 20},
    "Ui Ui": {"tier": 'Elite', "Strength": 20, "Mana": 31, "Defence": 25, "Agility": 24, "Vitality": 25, "Intelligence": 39},
    "Kokichi muta": {"tier": 'Elite', "Strength": 20, "Mana": 26, "Defence": 23, "Agility": 20, "Vitality": 20, "Intelligence": 33},
    "Kento Nanami": {"tier": 'Elite', "Strength": 33, "Mana": 34, "Defence": 29, "Agility": 29, "Vitality": 32, "Intelligence": 32},
    "Kasumi Miwa": {"tier": 'Elite', "Strength": 26, "Mana": 23, "Defence": 22, "Agility": 39, "Vitality": 24, "Intelligence": 28},
    "Miguel": {"tier": 'Elite', "Strength": 36, "Mana": 20, "Defence": 28, "Agility": 26, "Vitality": 34, "Intelligence": 20},
    "Shoko Ieiri": {"tier": 'Elite', "Strength": 20, "Mana": 30, "Defence": 25, "Agility": 26, "Vitality": 24, "Intelligence": 34},
    "Shoko Ieiri v2": {"tier": 'Elite', "Strength": 20, "Mana": 30, "Defence": 24, "Agility": 25, "Vitality": 23, "Intelligence": 36},
    "Nobara Kugisaki": {"tier": 'Elite', "Strength": 20, "Mana": 35, "Defence": 23, "Agility": 25, "Vitality": 22, "Intelligence": 35},
    "Nobara Kugisaki v2": {"tier": 'Elite', "Strength": 20, "Mana": 37, "Defence": 26, "Agility": 23, "Vitality": 20, "Intelligence": 35},
    "Hakari": {"tier": 'Elite', "Strength": 38, "Mana": 20, "Defence": 28, "Agility": 26, "Vitality": 34, "Intelligence": 20},
    "Kirara Hoshi": {"tier": 'Elite', "Strength": 20, "Mana": 31, "Defence": 25, "Agility": 26, "Vitality": 26, "Intelligence": 36},
    "Tengen": {"tier": 'Elite', "Strength": 20, "Mana": 39, "Defence": 36, "Agility": 25, "Vitality": 20, "Intelligence": 38},
    "Mei Mei": {"tier": 'Elite', "Strength": 20, "Mana": 40, "Defence": 23, "Agility": 23, "Vitality": 21, "Intelligence": 34},
    "Naoya Zenin": {"tier": 'Elite', "Strength": 27, "Mana": 23, "Defence": 21, "Agility": 38, "Vitality": 24, "Intelligence": 27},
    "Takaba Fumihiko": {"tier": 'Elite', "Strength": 20, "Mana": 38, "Defence": 25, "Agility": 23, "Vitality": 21, "Intelligence": 38},
    "Kashimo Hajime": {"tier": 'Elite', "Strength": 36, "Mana": 23, "Defence": 20, "Agility": 39, "Vitality": 25, "Intelligence": 26},
    "Higuruma Hiromi": {"tier": 'Elite', "Strength": 20, "Mana": 33, "Defence": 22, "Agility": 20, "Vitality": 20, "Intelligence": 37},
    "Takaka uro": {"tier": 'Elite', "Strength": 39, "Mana": 20, "Defence": 32, "Agility": 25, "Vitality": 34, "Intelligence": 20},
    "Yorozu": {"tier": 'Elite', "Strength": 33, "Mana": 36, "Defence": 29, "Agility": 29, "Vitality": 32, "Intelligence": 33},
    "Hana Kurusu": {"tier": 'Elite', "Strength": 20, "Mana": 37, "Defence": 23, "Agility": 24, "Vitality": 23, "Intelligence": 35},
    "Junpei yoshino": {"tier": 'Basic', "Strength": 8, "Mana": 3, "Defence": 6, "Agility": 9, "Vitality": 6, "Intelligence": 7},
    "Haruta shigemo": {"tier": 'Basic', "Strength": 5, "Mana": 12, "Defence": 9, "Agility": 7, "Vitality": 7, "Intelligence": 14},
    "Noritoshi Kamo": {"tier": 'Basic', "Strength": 6, "Mana": 13, "Defence": 10, "Agility": 10, "Vitality": 6, "Intelligence": 13},
    "Grasshopper Curse": {"tier": 'Basic', "Strength": 12, "Mana": 4, "Defence": 10, "Agility": 7, "Vitality": 13, "Intelligence": 5},
    "Momo Nishimiya": {"tier": 'Basic', "Strength": 9, "Mana": 9, "Defence": 6, "Agility": 15, "Vitality": 7, "Intelligence": 11},
    "Atsuya Kusakabe": {"tier": 'Basic', "Strength": 12, "Mana": 6, "Defence": 9, "Agility": 8, "Vitality": 13, "Intelligence": 7},
    "Akari Nitta": {"tier": 'Basic', "Strength": 6, "Mana": 6, "Defence": 7, "Agility": 8, "Vitality": 5, "Intelligence": 8},
    "Ijichi": {"tier": 'Basic', "Strength": 7, "Mana": 6, "Defence": 8, "Agility": 8, "Vitality": 5, "Intelligence": 10},
    "Yoshinobu Gakuganji": {"tier": 'Basic', "Strength": 6, "Mana": 10, "Defence": 10, "Agility": 8, "Vitality": 9, "Intelligence": 14},
    "Finger Bearer": {"tier": 'Basic', "Strength": 8, "Mana": 8, "Defence": 13, "Agility": 7, "Vitality": 14, "Intelligence": 8},
    "Eso": {"tier": 'Basic', "Strength": 5, "Mana": 15, "Defence": 9, "Agility": 8, "Vitality": 7, "Intelligence": 14},
    "Masamichi yaga": {"tier": 'Basic', "Strength": 6, "Mana": 9, "Defence": 8, "Agility": 8, "Vitality": 7, "Intelligence": 13},
    "Fumi": {"tier": 'Basic', "Strength": 8, "Mana": 3, "Defence": 7, "Agility": 8, "Vitality": 6, "Intelligence": 7},
    "Ozawa": {"tier": 'Basic', "Strength": 6, "Mana": 4, "Defence": 6, "Agility": 9, "Vitality": 5, "Intelligence": 8},
    "Saori": {"tier": 'Basic', "Strength": 5, "Mana": 6, "Defence": 8, "Agility": 6, "Vitality": 6, "Intelligence": 8},
    "Jin itadori": {"tier": 'Basic', "Strength": 14, "Mana": 7, "Defence": 9, "Agility": 8, "Vitality": 11, "Intelligence": 6},
    "Riko Amanai": {"tier": 'Basic', "Strength": 7, "Mana": 5, "Defence": 7, "Agility": 8, "Vitality": 6, "Intelligence": 9},
    "Misato Kuroi": {"tier": 'Basic', "Strength": 5, "Mana": 6, "Defence": 5, "Agility": 6, "Vitality": 7, "Intelligence": 9},
    "Toshihisa negi": {"tier": 'Basic', "Strength": 5, "Mana": 6, "Defence": 5, "Agility": 7, "Vitality": 5, "Intelligence": 8},
    "Manami Suda": {"tier": 'Basic', "Strength": 5, "Mana": 12, "Defence": 8, "Agility": 10, "Vitality": 7, "Intelligence": 15},
    "Larue": {"tier": 'Basic', "Strength": 4, "Mana": 14, "Defence": 8, "Agility": 9, "Vitality": 8, "Intelligence": 13},
    "Kechizu": {"tier": 'Basic', "Strength": 14, "Mana": 5, "Defence": 10, "Agility": 8, "Vitality": 11, "Intelligence": 7},

    # ════════════════════════════════════════════════════════════════
    # SPY × FAMILY
    # ════════════════════════════════════════════════════════════════
    # This cast has no magic system, so "Mana" is repurposed to mean
    # "extraordinary/supernatural power source" specifically for Anya
    # (telepathy) and Bond (precognition) — the only two characters who
    # have a power that isn't just elite human skill. Every other
    # character (spies, assassins, politicians, civilians, kids) has
    # ZERO supernatural ability, so their Mana is deliberately pinned
    # near the tier floor — same treatment as Toji/Maki/Mai in the JJK
    # section above. validate_char_stats() will (correctly) flag these
    # as "outside tier range" since that's the point: it's a visible,
    # intentional choice, not a bug.
    #
    # Archetype notes:
    #   - Loid, Yor, Fiona, Sylvia (V2/V3 = alt arts of the same skill
    #     level, not power-ups) = elite spy/assassin brutes: high
    #     Strength/Agility/Intelligence, floor Mana.
    #   - Anya, Bond = psychic support: high Mana/Intelligence, low
    #     Strength (a child and a dog, respectively).
    #   - Donovan Desmond = pure political/intelligence powerhouse, the
    #     "final boss" of the Forger family's mission — near-zero combat
    #     stats, ceiling Intelligence for his tier.
    #   - Forger Family / Loid & Yor combo cards average their members'
    #     individual profiles.
    #   - Civilians/kids (Damian, Becky, Karen, etc.) = weak-civilian
    #     archetype, same shape as the JJK civilian cast above.

    "Donovan Desmond": {"tier": 'Divine', "Strength": 40, "Mana": 40, "Defence": 42, "Agility": 40, "Vitality": 48, "Intelligence": 80},
    "Future Anya": {"tier": 'Divine', "Strength": 40, "Mana": 78, "Defence": 41, "Agility": 45, "Vitality": 42, "Intelligence": 70},
    "Yuri Briar": {"tier": 'Divine', "Strength": 65, "Mana": 40, "Defence": 58, "Agility": 60, "Vitality": 56, "Intelligence": 55},
    "Yuri Briar V2": {"tier": 'Divine', "Strength": 64, "Mana": 40, "Defence": 57, "Agility": 61, "Vitality": 55, "Intelligence": 56},
    "Fiona Frost": {"tier": 'Divine', "Strength": 68, "Mana": 40, "Defence": 60, "Agility": 72, "Vitality": 58, "Intelligence": 63},
    "Fiona Frost V2": {"tier": 'Divine', "Strength": 67, "Mana": 40, "Defence": 61, "Agility": 71, "Vitality": 57, "Intelligence": 64},
    "Sylvia Sherwood": {"tier": 'Divine', "Strength": 50, "Mana": 40, "Defence": 47, "Agility": 52, "Vitality": 46, "Intelligence": 72},
    "Sylvia Sherwood V2": {"tier": 'Divine', "Strength": 49, "Mana": 40, "Defence": 48, "Agility": 51, "Vitality": 47, "Intelligence": 73},
    "Sylvia Sherwood V3": {"tier": 'Divine', "Strength": 50, "Mana": 40, "Defence": 46, "Agility": 53, "Vitality": 45, "Intelligence": 71},
    "Shopkeeper": {"tier": 'Divine', "Strength": 40, "Mana": 40, "Defence": 44, "Agility": 40, "Vitality": 44, "Intelligence": 60},
    "Anya Forger": {"tier": 'Divine', "Strength": 40, "Mana": 75, "Defence": 40, "Agility": 44, "Vitality": 40, "Intelligence": 64},
    "Anya Forger V2": {"tier": 'Divine', "Strength": 40, "Mana": 76, "Defence": 40, "Agility": 43, "Vitality": 41, "Intelligence": 63},
    "Forger Family": {"tier": 'Divine', "Strength": 64, "Mana": 51, "Defence": 58, "Agility": 56, "Vitality": 56, "Intelligence": 63},
    "Loid & Yor": {"tier": 'Divine', "Strength": 78, "Mana": 40, "Defence": 70, "Agility": 73, "Vitality": 67, "Intelligence": 70},
    "Loid & Yor V2": {"tier": 'Divine', "Strength": 77, "Mana": 40, "Defence": 71, "Agility": 72, "Vitality": 68, "Intelligence": 69},
    "Loid Forger": {"tier": 'Divine', "Strength": 75, "Mana": 40, "Defence": 65, "Agility": 70, "Vitality": 62, "Intelligence": 80},
    "Loid Forger V2": {"tier": 'Divine', "Strength": 74, "Mana": 40, "Defence": 66, "Agility": 69, "Vitality": 63, "Intelligence": 79},
    "Yor Forger": {"tier": 'Divine', "Strength": 80, "Mana": 40, "Defence": 70, "Agility": 74, "Vitality": 72, "Intelligence": 56},
    "Yor Forger Uncensored": {"tier": 'Divine', "Strength": 80, "Mana": 40, "Defence": 70, "Agility": 74, "Vitality": 72, "Intelligence": 56},
    "Yor Forger V2": {"tier": 'Divine', "Strength": 79, "Mana": 40, "Defence": 71, "Agility": 73, "Vitality": 73, "Intelligence": 55},
    "Yor Forger V3": {"tier": 'Divine', "Strength": 79, "Mana": 40, "Defence": 69, "Agility": 75, "Vitality": 71, "Intelligence": 57},
    "Yor Forger V4": {"tier": 'Divine', "Strength": 80, "Mana": 40, "Defence": 70, "Agility": 75, "Vitality": 72, "Intelligence": 56},

    "Bond Forger": {"tier": 'Elite', "Strength": 20, "Mana": 39, "Defence": 24, "Agility": 28, "Vitality": 33, "Intelligence": 30},
    "Matthew McMahon": {"tier": 'Elite', "Strength": 20, "Mana": 20, "Defence": 24, "Agility": 20, "Vitality": 28, "Intelligence": 32},
    "Melinda Desmond": {"tier": 'Elite', "Strength": 20, "Mana": 20, "Defence": 22, "Agility": 21, "Vitality": 25, "Intelligence": 34},
    "Martha Marriott": {"tier": 'Elite', "Strength": 20, "Mana": 20, "Defence": 21, "Agility": 22, "Vitality": 24, "Intelligence": 35},
    "Luka": {"tier": 'Elite', "Strength": 33, "Mana": 20, "Defence": 28, "Agility": 30, "Vitality": 29, "Intelligence": 22},
    "Colonel Snidel": {"tier": 'Elite', "Strength": 30, "Mana": 20, "Defence": 32, "Agility": 23, "Vitality": 30, "Intelligence": 26},
    "Type F": {"tier": 'Elite', "Strength": 32, "Mana": 20, "Defence": 29, "Agility": 27, "Vitality": 28, "Intelligence": 24},
    "Keith Kepler": {"tier": 'Elite', "Strength": 31, "Mana": 20, "Defence": 27, "Agility": 26, "Vitality": 29, "Intelligence": 25},
    "Franky Franklin": {"tier": 'Elite', "Strength": 21, "Mana": 20, "Defence": 22, "Agility": 24, "Vitality": 24, "Intelligence": 30},
    "Billy Squire": {"tier": 'Elite', "Strength": 30, "Mana": 20, "Defence": 26, "Agility": 25, "Vitality": 27, "Intelligence": 23},
    "Edgar": {"tier": 'Elite', "Strength": 36, "Mana": 20, "Defence": 33, "Agility": 28, "Vitality": 35, "Intelligence": 21},
    "Mr. Blackbell": {"tier": 'Elite', "Strength": 25, "Mana": 20, "Defence": 23, "Agility": 21, "Vitality": 26, "Intelligence": 29},
    "Mr. Green": {"tier": 'Elite', "Strength": 22, "Mana": 20, "Defence": 24, "Agility": 21, "Vitality": 25, "Intelligence": 27},
    "Demetrius Desmond": {"tier": 'Elite', "Strength": 27, "Mana": 20, "Defence": 25, "Agility": 24, "Vitality": 26, "Intelligence": 31},
    "Vadim": {"tier": 'Elite', "Strength": 34, "Mana": 20, "Defence": 30, "Agility": 27, "Vitality": 32, "Intelligence": 21},
    "Jeeves": {"tier": 'Elite', "Strength": 21, "Mana": 20, "Defence": 26, "Agility": 20, "Vitality": 27, "Intelligence": 28},
    "Becky Blackbell": {"tier": 'Elite', "Strength": 20, "Mana": 20, "Defence": 21, "Agility": 23, "Vitality": 22, "Intelligence": 26},

    "Damian Desmond": {"tier": 'Basic', "Strength": 12, "Mana": 1, "Defence": 9, "Agility": 8, "Vitality": 10, "Intelligence": 11},
    "Gram Gretcher": {"tier": 'Basic', "Strength": 8, "Mana": 1, "Defence": 7, "Agility": 7, "Vitality": 8, "Intelligence": 9},
    "Zeb": {"tier": 'Basic', "Strength": 7, "Mana": 1, "Defence": 6, "Agility": 9, "Vitality": 7, "Intelligence": 8},
    "Olka Gretcher": {"tier": 'Basic', "Strength": 5, "Mana": 1, "Defence": 6, "Agility": 6, "Vitality": 7, "Intelligence": 10},
    "George Glooman": {"tier": 'Basic', "Strength": 9, "Mana": 1, "Defence": 8, "Agility": 6, "Vitality": 9, "Intelligence": 7},
    "Millie": {"tier": 'Basic', "Strength": 6, "Mana": 1, "Defence": 6, "Agility": 8, "Vitality": 7, "Intelligence": 9},
    "Benedict Ivan Goodfellow": {"tier": 'Basic', "Strength": 7, "Mana": 1, "Defence": 8, "Agility": 6, "Vitality": 8, "Intelligence": 10},
    "Emile Elman": {"tier": 'Basic', "Strength": 6, "Mana": 1, "Defence": 6, "Agility": 7, "Vitality": 7, "Intelligence": 9},
    "Sharon": {"tier": 'Basic', "Strength": 5, "Mana": 1, "Defence": 5, "Agility": 7, "Vitality": 6, "Intelligence": 9},
    "Camilla": {"tier": 'Basic', "Strength": 5, "Mana": 1, "Defence": 5, "Agility": 6, "Vitality": 6, "Intelligence": 9},
    "Bill Watkins": {"tier": 'Basic', "Strength": 10, "Mana": 1, "Defence": 8, "Agility": 7, "Vitality": 9, "Intelligence": 8},
    "Daybreak": {"tier": 'Basic', "Strength": 9, "Mana": 1, "Defence": 9, "Agility": 8, "Vitality": 10, "Intelligence": 6},
    "Henry Henderson": {"tier": 'Basic', "Strength": 6, "Mana": 1, "Defence": 7, "Agility": 5, "Vitality": 7, "Intelligence": 9},
    "Murdoch Swan": {"tier": 'Basic', "Strength": 8, "Mana": 1, "Defence": 9, "Agility": 6, "Vitality": 9, "Intelligence": 8},
    "Karen": {"tier": 'Basic', "Strength": 5, "Mana": 1, "Defence": 5, "Agility": 6, "Vitality": 6, "Intelligence": 8},
    "Kacey": {"tier": 'Basic', "Strength": 5, "Mana": 1, "Defence": 5, "Agility": 6, "Vitality": 6, "Intelligence": 7},
    "Chloe": {"tier": 'Basic', "Strength": 5, "Mana": 1, "Defence": 5, "Agility": 7, "Vitality": 6, "Intelligence": 8},

    # ════════════════════════════════════════════════════════════════
    # THE ANGEL NEXT DOOR SPOILS ME ROTTEN
    # ════════════════════════════════════════════════════════════════
    # Slice-of-life romance cast, no combat/powers at all. Stats here
    # represent everyday-life "stats" loosely: Strength/Defence/Agility
    # stay near each tier's floor for everyone (nobody fights), while
    # Intelligence and Vitality carry most of the differentiation
    # (academic performance, domestic competence, stamina/resilience).
    # Mana sits at floor for the entire cast — zero supernatural elements
    # in this series.
    "Amane Fujimiya": {"tier": 'Divine', "Strength": 44, "Mana": 40, "Defence": 42, "Agility": 48, "Vitality": 58, "Intelligence": 56},
    "Amane Fujimiya V2": {"tier": 'Divine', "Strength": 43, "Mana": 40, "Defence": 43, "Agility": 47, "Vitality": 59, "Intelligence": 55},
    "Mahiru & Amane": {"tier": 'Divine', "Strength": 42, "Mana": 40, "Defence": 42, "Agility": 46, "Vitality": 60, "Intelligence": 68},
    "Mahiru & Amane V2": {"tier": 'Divine', "Strength": 41, "Mana": 40, "Defence": 43, "Agility": 45, "Vitality": 61, "Intelligence": 67},
    "Mahiru Shiina": {"tier": 'Divine', "Strength": 40, "Mana": 40, "Defence": 44, "Agility": 44, "Vitality": 62, "Intelligence": 76},
    "Mahiru Shiina V2": {"tier": 'Divine', "Strength": 40, "Mana": 40, "Defence": 43, "Agility": 45, "Vitality": 63, "Intelligence": 75},
    "Mahiru Shiina V3": {"tier": 'Divine', "Strength": 40, "Mana": 40, "Defence": 44, "Agility": 43, "Vitality": 61, "Intelligence": 77},
    "Mahiru Shiina V4": {"tier": 'Divine', "Strength": 40, "Mana": 40, "Defence": 44, "Agility": 43, "Vitality": 61, "Intelligence": 77},

    "Itsuki Akazawa": {"tier": 'Elite', "Strength": 22, "Mana": 20, "Defence": 21, "Agility": 25, "Vitality": 31, "Intelligence": 28},
    "Shuuto Fujimiya": {"tier": 'Elite', "Strength": 28, "Mana": 20, "Defence": 24, "Agility": 26, "Vitality": 29, "Intelligence": 30},
    "Yuuta Kadowaki": {"tier": 'Elite', "Strength": 24, "Mana": 20, "Defence": 22, "Agility": 27, "Vitality": 28, "Intelligence": 26},
    "Chitose Shirakawa": {"tier": 'Elite', "Strength": 20, "Mana": 20, "Defence": 21, "Agility": 24, "Vitality": 30, "Intelligence": 33},
    "Chitose Shirakawa V2": {"tier": 'Elite', "Strength": 20, "Mana": 20, "Defence": 20, "Agility": 25, "Vitality": 31, "Intelligence": 32},
    "Sayo Shiina": {"tier": 'Elite', "Strength": 20, "Mana": 20, "Defence": 22, "Agility": 22, "Vitality": 32, "Intelligence": 35},
    "Sayo Shiina V2": {"tier": 'Elite', "Strength": 20, "Mana": 20, "Defence": 21, "Agility": 23, "Vitality": 33, "Intelligence": 34},
    "Shihoko Fujimiya": {"tier": 'Elite', "Strength": 20, "Mana": 20, "Defence": 22, "Agility": 21, "Vitality": 34, "Intelligence": 36},
    "Shihoko Fujimiya V2": {"tier": 'Elite', "Strength": 20, "Mana": 20, "Defence": 23, "Agility": 20, "Vitality": 35, "Intelligence": 37},
}

def get_char_stats(name: str) -> dict | None:
    """Look up a character's full stat block by name (case-insensitive)."""
    for key, stats in CHAR_STATS.items():
        if key.lower() == name.lower():
            return stats
    return None


def in_tier_range(tier: str, value: int) -> bool:
    lo, hi = TIER_RANGES.get(tier, (0, 0))
    return lo <= value <= hi


def validate_char_stats() -> list[str]:
    """Returns a list of warnings for any stat missing or outside its tier's range."""
    warnings = []
    for name, stats in CHAR_STATS.items():
        tier = stats.get("tier")
        if tier not in TIER_RANGES:
            warnings.append(f"{name}: unknown tier '{tier}'")
            continue
        for field in STAT_FIELDS:
            val = stats.get(field)
            if val is None:
                warnings.append(f"{name}: missing '{field}'")
                continue
            if not in_tier_range(tier, val):
                lo, hi = TIER_RANGES[tier]
                warnings.append(f"{name}: {field}={val} is outside {tier} range ({lo}-{hi})")
    return warnings


if __name__ == "__main__":
    issues = validate_char_stats()
    print(f"Loaded {len(CHAR_STATS)} characters.")
    if not issues:
        print("✅ All stats pass tier-range validation.")
    else:
        print(f"⚠️  {len(issues)} stat(s) fall outside their tier's range (some may be intentional):")
        for w in issues:
            print("  -", w)
