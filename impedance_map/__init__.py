"""Impedance Map — simulateur 2D quasi-statique d'impédance le long des pistes KiCad.

Sous-paquets :
    solver      solveur électrostatique 2D autonome (aucune dépendance KiCad)
    stackup     modèle d'empilement (board KiCad, JSON perso, préréglages JLCPCB)
    extraction  lecture du board (kipy en direct ou fichier .kicad_pcb) et construction des coupes
    viz         overlay dans l'éditeur PCB et rapport HTML
    ui          dialogue (wxPython, repli tkinter)
"""

__version__ = "0.3.1"
