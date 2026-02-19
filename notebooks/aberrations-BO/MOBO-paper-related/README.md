This folder corresponds to the paper:
![alt text](final.png)
[Towards Self-Optimizing Electron Microscope: Robust Tuning of Aberration Coefficients via Physics-Aware Multi-Objective Bayesian Optimization](https://arxiv.org/abs/2601.18972)



Q1. How to setup Python environment? 
- Please use the pyproject.toml in this repository
- command : uv sync
- Note : Setting up the environment for asyncroscopy can be tricky as the Autoscript part will need the the python wheels from Thermofisher -> Please watch this video for that part :
    - https://www.youtube.com/watch?v=EYPrCUtKUmI&t=1032s
- Feel free to email us if you get stuck

Q2. Which notebook is on simulation?
- notebooks/aberrations-BO/MOBO-paper-related/notebooks/Tuning-aberrations-SImulation-STEM.ipynb

Q3. Which notebooks are live on Mic?
- There is a script for MOBO - notebooks/aberrations-BO/MOBO-paper-related/scripts/c1-a1-b2-a2.py
- There is notebook to set BO parametrs (no MOBO tuning) - notebooks/aberrations-BO/MOBO-paper-related/notebooks/Aberrations_real.ipynb

Q4. What's next?
- Well --> we will make a nice UI probably
- We will be grateful for any suggestions