import itertools
import collections

import numpy as np
import scipy.interpolate
import scipy.spatial

from .tools import find_k_order_delaunay_neighbours, find_vector_index

def group_lattice_vectors_by_length(lattice):
    """
    Group an array of vectors by length.
    WARNING: This function uses rounding and should only
    be used with lattices of scales larger than 1.

    Deprecated.

    :param lattice: vectors to group

    :type lattice: numpy.ndarray

    :rtype: dict[float, numpy.ndarray]
    """

    lattice = np.array(sorted(lattice, key=lambda vec: np.dot(vec, vec)))
    ordered_lattice = collections.defaultdict(list)
    for vec in lattice:
        length = np.round(np.dot(vec, vec), 2)
        ordered_lattice[length].append(vec)
    ordered_lattice = {k: np.array(v) for k,v in ordered_lattice.items()}
    return ordered_lattice

def generate_lattice(lattice_basis, size):
    """
    Combine lattice basis vectors *size* times to generate a lattice

    :param lattice_basis: basis vectors of the lattice
    :param size: maximum coefficient size

    :type lattice_basis: numpy.ndarray
    :type size: int

    :rtype: numpy.ndarray
    """

    dimension = len(lattice_basis)
    combinations = np.array(list(itertools.product(range(-size, size+1), repeat=dimension)))
    lattice = np.matmul(lattice_basis.T, combinations.T)
    return lattice.T

def generate_lattice_by_shell(lattice_basis, shell):
    """
    Generate lattice from basis vectors with *shell* shells around the zero vector

    :param lattice_basis: basis vectors of the lattice
    :param shell: number of shells

    :type lattice_basis: numpy.ndarray
    :type shell: int

    :rtype: numpy.ndarray
    """

    uncut_lattice = generate_lattice(lattice_basis, shell)
    triangulation = scipy.spatial.Delaunay(uncut_lattice) #pylint: disable=E1101

    # There has to be a zero vector in the lattice, which should always be the center
    zero_vec_index = find_vector_index(uncut_lattice, [0,]*uncut_lattice.shape[1])

    lattice_indices = find_k_order_delaunay_neighbours(zero_vec_index, triangulation, shell)
    return uncut_lattice[lattice_indices]

def generate_reciprocal_lattice_basis(lattice_basis):
    """
    Generate the reciprocal lattice basis of a given basis.

    :param lattice_basis: lattice basis to transform

    :type lattice_basis: numpy.ndarray

    :rtype: numpy.ndarray
    """

    return 2*np.pi*np.linalg.inv(lattice_basis.T)

def generate_rotation_matrix(angle, degrees=True):
    """
    Generate a 2D rotation matrix for given *angle*

    :param angle: the angle to rotate by
    :param degrees: if angle is given in degrees or not (radians)

    :type angle: float
    :type degrees: bool

    :rtype: numpy.ndarray
    """

    if degrees:
        angle *= np.pi/180
    return np.array([[np.cos(angle), -np.sin(angle)],
        [np.sin(angle), np.cos(angle)]])

def rotate_lattice(lattice, angle, degrees=True):
    """
    Rotate a 2D lattice by *angle*

    :param lattice: lattice to rotate
    :param angle: the angle to rotate by
    :param degrees: if angle is given in degrees or not (radians)

    :type lattice: numpy.ndarray
    :type angle: float
    :type degrees: bool

    :rtype: numpy.ndarray
    """

    rotation_matrix = generate_rotation_matrix(angle)
    rotated_lattice = np.matmul(rotation_matrix, lattice.T).T
    return rotated_lattice

def generate_twisted_lattice_by_shell(lattice_basis1, lattice_basis2, twist_angle, shell):
    """
    Generate a twisted moire lattice from two sets of basis vectors

    :param lattice_basis1: lattice basis of first lattice
    :param lattice_basis2: lattice basis of second lattice
    :param twist_angle: twist angle
    :param shell: number of shells to calculate

    :type lattice_basis1: numpy.ndarray
    :type lattice_basis2: numpy.ndarray
    :type twist_angle: float
    :type shell: int

    :rtype: numpy.ndarray
    """

    lattice1 = generate_lattice_by_shell(lattice_basis1, shell)
    lattice2 = generate_lattice_by_shell(lattice_basis2, shell)
    lattice2 = rotate_lattice(lattice2, twist_angle)
    return np.stack([lattice1, lattice2])

def generate_k_path(points, N):
    """
    Interpolate between *points* with *N* steps

    :param points: points to interpolate between
    :param N: sample number

    :type points: numpy.ndarray
    :type N: int

    :rtype: numpy.ndarray
    """

    num_points = len(points)
    path = scipy.interpolate.griddata(np.arange(num_points), points, np.linspace(0, num_points-1, N))
    return path

def generate_monkhorst_pack_raw(lattice_basis, q):
    """
    Generate a Monkhorst-Pack set naively, which expands into neighbouring
    Brillouin zones. Refer to :py:func:`generate_monkhorst_pack_set` for 
    the folded Monkhorst-Pack set. Use this one for calculations and the other
    for plots.

    :param lattice_basis: lattice basis
    :param q: lattice density (see the `original paper <https://journals.aps.org/prb/pdf/10.1103/PhysRevB.13.5188>`_)

    :type lattice_basis: numpy.ndarray
    :type q: int

    :rtype: numpy.ndarray
    """

    dimension = len(lattice_basis)
    p = np.arange(1, q+1)
    n = (2*p-q-1) / (2*q)
    combinations = np.array(list(itertools.product(n, repeat=dimension)))
    raw_monkhorst_pack_set = np.matmul(lattice_basis.T, combinations.T).T

    lengths = np.sum(raw_monkhorst_pack_set**2, axis=1)
    shift = raw_monkhorst_pack_set[np.argmin(lengths)]
    return raw_monkhorst_pack_set - shift

def generate_monkhorst_pack_set(lattice_basis, q):
    """
    Generate a folded Monkhorst-Pack set, which only fills the first
    Brillouin zone.
    
    :param lattice_basis: lattice basis
    :param q: lattice density (see the `original paper <https://journals.aps.org/prb/pdf/10.1103/PhysRevB.13.5188>`_)

    :type lattice_basis: numpy.ndarray
    :type q: int

    :rtype: numpy.ndarray
    """

    raw_monkhorst_pack_set = generate_monkhorst_pack_raw(lattice_basis, q)
    lattice_1bz = generate_lattice_by_shell(lattice_basis, 1)
    tree = scipy.spatial.KDTree(lattice_1bz)

    # find nearest neighbour lattice vector for each k vector of
    # the Monkhorst-Pack set
    k_positions = np.stack(tree.query(raw_monkhorst_pack_set))
    position_dict = collections.defaultdict(list)
    for neighbour, k_vector in zip(k_positions[1], raw_monkhorst_pack_set):
        position_dict[neighbour].append(k_vector)

    # Shift all points back to first Brillouin zone
    monkhorst_pack_set = np.vstack([position_dict[neighbour]-tree.data[int(neighbour)]
        for neighbour in position_dict.keys()])
    return monkhorst_pack_set






