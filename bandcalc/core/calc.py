import cmath

import cupy as cp
import numpy as np
import scipy.constants
import scipy.spatial

#import ray
#ray.init(address='auto', _redis_password='5241590000000000', ignore_reinit_error=True)

from numba import cuda

from .generate import generate_k_path
from .tools import find_vector_index, find_nearest_delaunay_neighbours

hbar = scipy.constants.physical_constants["Planck constant over 2 pi"][0]
e = scipy.constants.physical_constants["elementary charge"][0]
#m_e = 0.42*scipy.constants.physical_constants["electron mass"][0]
#m_h = 0.34*scipy.constants.physical_constants["electron mass"][0]
#m = m_e+m_h
m = 0.35*scipy.constants.physical_constants["electron mass"][0]
V = 0

def eps_0(k, G, m):
    r"""
    Calculate the unpertubated energy for given k vector and reciprocal lattice vector using

    .. math::
        \varepsilon^{(0)}_{\vec{G}_0}(\vec{k}\,) = \frac{\hbar^2}{2m}\left(\vec{k} - \vec{G}_0\right)^2

    Expects input vectors to be in units 1/nm.

    :param k: k vector of the particle
    :param G: reciprocal lattice vector
    :param m: particle mass

    :type k: numpy.ndarray
    :type G: numpy.ndarray
    :type m: float

    :rtype: float
    """

    return hbar**2/(2*m)*np.sum((k-G)**2, axis=1)*1e18/e # in eV

def calc_hamiltonian(lattice, potential_matrix, m):
    """
    Construct the hamiltonian in a specified lattice as a function of k

    :param lattice: reciprocal lattice
    :param m: particle mass

    :type lattice: numpy.ndarray
    :type m: float

    :rtype: function
    """

    diagonal = lambda k: np.diag(eps_0(k, lattice, m))
    return lambda k: potential_matrix + diagonal(k)

def calc_potential_matrix(lattice, potential_fun=None, use_gpu=False, num_gpus=1, **kwargs):
    """
    Calculate matrix of potentials using *potential_fun*.

    :param lattice: reciprocal lattice
    :param potential_fun: function that calculates potential for a set of lattice vectors.
                          Has to be a `function` or an `int`/`float`
                          for a constant potential

    :type lattice: numpy.ndarray
    :type potential_fun: function | int | float

    :rtype: numpy.ndarray
    """

    if potential_fun is None:
        potential_matrix = np.zeros((lattice.shape[0],)*2)
    elif isinstance(potential_fun, (float, int)):
        potential_matrix = np.ones((lattice.shape[0],)*2)*potential_fun
        np.fill_diagonal(potential_matrix, 0)
    elif callable(potential_fun):
        if use_gpu:
            xp = cp
            potential_fun = ray.remote(num_gpus=num_gpus)(potential_fun)
            lattice = cp.array(lattice)
            kwargs = {k: cp.array(v) if isinstance(v, np.ndarray) else v
                    for k,v in kwargs.items()}
        else:
            xp = np
            potential_fun = ray.remote(potential_fun)
        lattice_matrix = xp.array([lattice - vec for vec in lattice])
        potential_matrix = xp.array(
                ray.get([potential_fun.remote(lattice, use_gpu=use_gpu, **kwargs)
                    for lattice in lattice_matrix])
        )

    return cp.asnumpy(potential_matrix)

def calc_potential_matrix_from_coeffs(lattice, coeffs):
    """
    Calculates matrix of potentials using Fourier coefficients.

    :param lattice: reciprocal lattice
    :param coeffs: Fourier coefficients

    :type lattice: numpy.ndarray
    :type coeffs: list | numpy.ndarray

    :rtype: numpy.ndarray
    """

    triangulation = scipy.spatial.Delaunay(lattice) #pylint: disable=E1101
    potential_matrix = np.zeros((len(lattice),)*2, dtype=complex)

    zero_vec_index = find_vector_index(lattice, [0,]*lattice.shape[1])
    if zero_vec_index is None:
        raise Exception("Could not locate zero vector: Can't compute "\
                "potential matrix")
    zero_vec_neighbours = list(find_nearest_delaunay_neighbours(zero_vec_index, triangulation))
    angles = np.sort(np.round(np.angle(lattice[zero_vec_neighbours].view(complex)), 4)[:,0])
    angles_dict = dict(zip(angles, coeffs))

    for i, lat_vec in enumerate(lattice):
        neighbours = list(find_nearest_delaunay_neighbours(i, triangulation))
        coefficients = [angles_dict[np.round(np.angle(
            (lattice[neighbour]-lat_vec).view(complex)),4)[0]] for neighbour in neighbours]
        potential_matrix[i, neighbours] = coefficients
    return potential_matrix

def calc_bandstructure(k_points, N, hamiltonian):
    """
    Calculate the band structure of a hamiltonian along a given k path with N samples

    :param k_points: k points
    :param N: number of samples
    :param hamiltonian: the hamiltonian to calculate the bandstructure with
        (as a function of k)

    :type k_points: numpy.ndarray
    :type N: int
    :type hamiltonian: function

    :rtype: numpy.ndarray
    """

    path = generate_k_path(k_points, N)
    eig_vals = np.array(
            [np.linalg.eigvals(hamiltonian(k)) for k in path]
    )
    return eig_vals

def calc_eigenvector_for_bandindex(matrix, band_index=0):
    """
    Calculate the eigenvectors of a matrix and sort them by their corresponding eigenvalues.
    Return the *band_index*-th eigenvector.

    :param matrix: Calculate the eigenvectors from this matrix
    :param band_index: this is the index of the band which the eigenvector belongs to.
        Starts from band with lowest energy

    :type matrix: numpy.ndarray
    :type band_index: int

    :rtype: numpy.ndarray
    """
    try:
        eig_vals, eig_vecs = np.linalg.eig(matrix)
    except Exception as e:
        print(f"Could not calculate eigenvectors and eigenvalues. Error:\n{e}")
        return None
    eig_vecs = eig_vecs[:, np.argsort(eig_vals)]
    try:
        eig_vec = eig_vecs[:, band_index]
    except IndexError:
        print(f"Band index can only be in the range 0..{eig_vecs.shape[1]-1}")
        return None
    return eig_vec

def calc_wave_function_on_grid(k_point, lattice, grid, hamiltonian, band_index=0):
    r"""
    Calculate the wave function (not the absolute square) of a system on a real space grid.
    It is assumed, that the wave function :math:`|\chi\rangle` can be written as

    .. math::
        |\chi_{\mathbf{Q}}\rangle^{(\alpha)}(\mathbf{r}) = \sum_{\mathbf{G}^\text{M}}
        c^{(\alpha)}_{\mathbf{Q}-\mathbf{G}^\text{M}}
        \text{e}^{\text{i}(\mathbf{Q}-\mathbf{G}^\text{M})\mathbf{r}},

    where
     * :math:`\alpha\in \{0, ..., N-1\}` is the *band_index* of the wave function.
       If there are :math:`N` reciprocal lattice vectors, there will be :math:`N` band indices.
     * :math:`\mathbf{G}^{\text{M}}` are the reciprocal *lattice* vectors
     * :math:`\mathbf{r}` are real space vectors (*grid*)
     * :math:`\mathbf{Q}` is the *k_point*, at which the wave function will be evaluated.
     * :math:`c` are the eigenvector solutions of the systems hamiltonian.
       The eigenvectors are sorted by :math:`\alpha` and their components can be referred to
       by the corresponding lattice vectors.

    :param k_point: :math:`\mathbf{Q}`
    :param lattice: :math:`\mathbf{G}^{\text{M}}`
    :param grid: :math:`\mathbf{r}`
    :param hamiltonian: Hamiltonian to calculate the eigenstates with
        (as a function of k)
    :param band_index: :math:`\alpha`

    :type k_point: numpy.ndarray
    :type lattice: numpy.ndarray
    :type grid: list(numpy.ndarray)
    :type hamiltonian: function
    :type band_index: int

    :rtype: numpy.ndarray
    """

    eig_vec = calc_eigenvector_for_bandindex(hamiltonian(k_point), band_index)

    summand = np.exp(1j*(
        np.tensordot((k_point-lattice)[:,0], grid[0], axes=0)+
        np.tensordot((k_point-lattice)[:,1], grid[1], axes=0)
        ))*eig_vec[..., None, None]
    return np.sum(summand, axis=0)

def calc_moire_potential_on_grid(grid, reciprocal_moire_lattice, potential_coeffs):
    r"""
    Calculate the moire potential on a regular grid using

    .. math::
        V^{\text{M}}(\vec{r}) \approx \sum_{j=1}^6 V_j \exp\left(\text{i}\vec{G}_j^{\text{M}}\vec{r}\right)

    :param grid: :math:`\vec{r}`, a numpy meshgrid to calculate the potential on
    :param moire_lattice: :math:`\vec{G}_j^{\text{M}}`, the six moire lattice vectors
    :param potential_coeffs: :math:`V_j`, the coefficients for the potential

    :type grid: list(numpy.ndarray)
    :type moire_lattice: numpy.ndarray
    :type potential_coeffs: numpy.ndarray

    :rtype: numpy.ndarray
    """

    moire_potential = np.sum(np.exp(1j*(
        np.tensordot(reciprocal_moire_lattice[:,0], grid[0], axes=0)+
        np.tensordot(reciprocal_moire_lattice[:,1], grid[1], axes=0)
        ))*potential_coeffs[:, None, None], axis=0)
    return moire_potential

def calc_moire_potential(r, reciprocal_moire_lattice, potential_coeffs):
    r"""
    Calculate the moire potential on a set of points using

    .. math::
        V^{\text{M}}(\vec{r}) \approx \sum_{j=1}^6 V_j \exp\left(\text{i}\vec{G}_j^{\text{M}}\vec{r}\right)

    :param r: :math:`\vec{r}`, set of points
    :param moire_lattice: :math:`\vec{G}_j^{\text{M}}`, the six moire lattice vectors
    :param potential_coeffs: :math:`V_j`, the coefficients for the potential

    :type r: numpy.ndarray
    :type moire_lattice: numpy.ndarray
    :type potential_coeffs: numpy.ndarray

    :rtype: numpy.ndarray
    """

    moire_potential = np.sum(np.exp(1j*(
        np.tensordot(reciprocal_moire_lattice[:,0], r[:,0], axes=0)+
        np.tensordot(reciprocal_moire_lattice[:,1], r[:,1], axes=0)
        ))*potential_coeffs[:, None], axis=0)
    return moire_potential

def calc_moire_potential_reciprocal_on_grid(real_space_points, reciprocal_space_grid, moire_potential_pointwise):
    r"""
    Calculate the reciprocal moire potential on a grid using

    .. math::
        V^{\text{M}}_{G_{\text{M}}} = \frac{1}{A}\int_{\text{MWSC}}
        V_{\text{M}}(\vec{r}\,)\text{e}^{-\text{i}G_{\text{M}}\vec{R}}\text{d}r^2

    with MWSC being the first Moire Wigner Seitz cell.

    :param real_space_points: Real space sample points in the MWSC (for example a Monkhorst-Pack grid)
    :param reciprocal_space_grid: Grid of reciprocal vectors :math:`G_{\text{M}}`
    :param moire_potential_pointwise: Pre-calculated real space Moire potential :math:`V^{\text{M}}(\vec{r}\,)`

    :type real_space_points: numpy.ndarray
    :type reciprocal_space_grid: list(numpy.ndarray)
    :type moire_potential_pointwise: numpy.ndarray

    :rtype: numpy.ndarray
    """

    integrand = np.exp(
            -1j*(
                np.tensordot(real_space_points[:,0], reciprocal_space_grid[0], axes=0) + 
                np.tensordot(real_space_points[:,1], reciprocal_space_grid[1], axes=0)
            ))*moire_potential_pointwise[..., None, None]
    integral = integrand.sum(axis=0)
    return integral/len(real_space_points)

def calc_moire_potential_reciprocal(reciprocal_space_points, real_space_points, moire_potential_pointwise,
        **kwargs):
    r"""
    Calculate the reciprocal moire potential using

    .. math::
        V^{\text{M}}_{G_{\text{M}}} = \frac{1}{A}\int_{\text{MWSC}}
        V_{\text{M}}(\vec{r}\,)\text{e}^{-\text{i}G_{\text{M}}\vec{R}}\text{d}r^2

    with MWSC being the first Moire Wigner Seitz cell.

    :param real_space_points: Real space sample points in the MWSC (for example a Monkhorst-Pack grid)
    :param reciprocal_space_grid: Reciprocal vectors :math:`G_{\text{M}}`
    :param moire_potential_pointwise: Pre-calculated real space Moire potential :math:`V^{\text{M}}(\vec{r}\,)`

    :type real_space_points: numpy.ndarray
    :type reciprocal_space_grid: numpy.ndarray
    :type moire_potential_pointwise: numpy.ndarray

    :rtype: numpy.ndarray
    """

    xp = cp if kwargs.get("use_gpu", False) else np

    integrand = xp.exp(
            -1j*(
                xp.tensordot(real_space_points[:,0], reciprocal_space_points[:,0], axes=0) +
                xp.tensordot(real_space_points[:,1], reciprocal_space_points[:,1], axes=0)
            ))*moire_potential_pointwise[..., None]
    integral = integrand.sum(axis=0)
    return integral/len(real_space_points)

@cuda.jit(device=True)
def _wannier_summand_gpu(c_alpha, Q, GM, r, R):
    r"""
    Calculate a summand

    ..math::
        c^{\alpha}_{\mathbf{Q}-\mathbf{G}^{\text{M}}}\text{e}^{-\text{i}
        \mathbf{G}^{\text{M}}\mathbf{r}}\text{e}^{-\text{i}\mathbf{Q}(\mathbf{r}-\mathbf{R}}

    for the wannier function.
    """

    return (c_alpha
            *cmath.exp(
                -1j*(
                    GM[0]*r[0] + GM[1]*r[1]
                    )
                )
            *cmath.exp(
                1j*(
                    Q[0]*(r[0]-R[0]) + Q[1]*(r[1]-R[1])
                    )
                )
            )

@cuda.jit
def _wannier_gpu(res, c_alpha, Q, GM, r, R):
    r"""
    Calculate :py:func`summand` for every :math:`\mathbf{Q}` and
    :math:`\mathbf{G}^\text{M}`.
    """

    i, j, k = cuda.grid(3) #pylint: disable=E0633,E1121

    if i < res.shape[0] and j < res.shape[1] and k < res.shape[2]:
        res[i, j, k] = _wannier_summand_gpu(c_alpha[j, k], Q[j], GM[k], r[i], R)

def calc_wannier_function_gpu(hamiltonian, k_points, reciprocal_lattice_vectors, r, R,
        band_index=0):
    r"""
    Calculate the wannier function

    .. math::
        w^{(\alpha)}(\mathbf{r} - \mathbf{R}) = \sum_{\mathbf{Q}, \mathbf{G}^\text{M}}
        c^{(\alpha)}_{\mathbf{Q}-\mathbf{G}^{\text{M}}}
        \text{e}^{-\text{i}\mathbf{G}^{\text{M}}\mathbf{r}}
        \text{e}^{-\text{i}\mathbf{Q}(\mathbf{r}-\mathbf{R})}

    where

     * the :math:`c^{(\alpha)}_{\mathbf{Q}-\mathbf{G}^{\text{M}}}` are
       obtained from the *hamiltonian* evaluated at each :math:`\mathbf{Q} \in` *k_points*.
       These are the Bloch functions coefficients.
     * :math:`\mathbf{Q} \in` *k_points*
     * :math:`\mathbf{G}^{\text{M}} \in` *reciprocal_lattice_vectors*
     * :math:`\alpha` is the *band_index*

    :param hamiltonian: the systems hamiltonian
    :param k_points: k-space samples to calculate the wannier function from
    :param reciprocal_lattice_vectors: reciprocal lattice vectors
    :param r: array of points in real space to calculate the wannier function on
    :param R: real space lattice vector around which the wannier function is centered

    :type hamiltonian: function
    :type k_points: numpy.ndarray
    :type reciprocal_lattice_vectors: numpy.ndarray
    :type r: numpy.ndarray
    :type R: numpy.ndarray
    """

    dtype = np.float32

    # Calculate Bloch coefficients c_alpha at every k point
    c_alpha = []
    for k_point in k_points:
        eig_vec = calc_eigenvector_for_bandindex(hamiltonian(k_point), band_index)
        c_alpha.append(eig_vec)
    c_alpha = np.array(c_alpha)

    # Transfer all variables to GPU
    k_points_gpu = cuda.to_device(k_points.astype(dtype))
    reciprocal_lattice_vectors_gpu = cuda.to_device(reciprocal_lattice_vectors.astype(dtype))
    c_alpha_gpu = cuda.to_device(c_alpha.astype(np.complex64))
    r_gpu = cuda.to_device(r.astype(dtype))
    R_gpu = cuda.to_device(R.astype(dtype))

    # Prepare result array and thread topology
    result_gpu = cuda.device_array((len(r), len(k_points),
        len(reciprocal_lattice_vectors)), dtype=np.complex64)
    threadsperblock = (32, 32, 1)
    blockspergrid = tuple(
            result_gpu.shape[i] // threadsperblock[i]
            if result_gpu.shape[i] % threadsperblock[i] == 0
            else result_gpu.shape[i] // threadsperblock[i] + 1 for i in range(3)
        )

    # Calculate wannier components on GPU
    _wannier_gpu[blockspergrid, threadsperblock](result_gpu, c_alpha_gpu, #pylint: disable=E1136
            k_points_gpu, reciprocal_lattice_vectors_gpu, r_gpu, R_gpu)

    # Get result and free GPU memory
    result = result_gpu.copy_to_host()
    del result_gpu

    wannier_function = np.sum(result, axis=(1,2))
#    wannier_function = wannier_function/np.sum(np.abs(wannier_function)**2)
    return wannier_function
