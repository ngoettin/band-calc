import numpy as np
import matplotlib.pyplot as plt
from scipy.spatial import Voronoi, voronoi_plot_2d #pylint: disable=E0611

import bandcalc

# Constants
a = 1
V = 1
N = 1000

# Reciprocal lattice vectors
b1 = np.array([2*np.pi/a, 0])
b2 = np.array([0, 2*np.pi/a])
b = np.vstack([b1, b2])
lattice = bandcalc.generate_lattice_by_shell(b, 2)

# k path
points = np.array([[0, 0],  # Gamma
    [np.pi/a, 0],           # X
    [np.pi/a, np.pi/a],     # M
    [0, 0]])                # Gamma
k_names = [r"$\Gamma$", r"X", r"M", r"$\Gamma$"]
path = bandcalc.generate_k_path(points, N)

# Calculate Brillouin zones
vor = Voronoi(lattice)

# Calculate band structure
bandstructure = bandcalc.calc_bandstructure(points, lattice, N)

# Plots
fig, ax = plt.subplots(nrows=1, ncols=2, figsize=(11,5))
bandcalc.plot_lattice(ax[0], lattice, "ko")
bandcalc.plot_k_path(ax[0], path, "r")
voronoi_plot_2d(vor, ax[0], show_points=False, show_vertices=False)
bandcalc.plot_bandstructure(ax[1], bandstructure, k_names, "k")

ax[1].set_ylim([-1, 30])

plt.tight_layout()
plt.show()