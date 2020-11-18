"""
This module contains solver routines for fitting models to the timeseries
of stations.
"""

import numpy as np
import scipy as sp
import scipy.sparse as sparse
import cvxpy as cp
import cartopy.geodesic as cgeod
from warnings import warn
from tqdm import tqdm

from .config import defaults
from .tools import weighted_median


def _combine_mappings(ts, models, reg_indices=False, cached_mapping=None, init_reweights=None):
    """
    Quick helper function that concatenates the mapping matrices of the
    models given the timevector in ts, and returns the relevant sizes.
    If reg_indices = True, also return an array indicating which model
    is set to be regularized.
    It also makes sure that G only contains columns that contain at least
    one non-zero element, and correspond to parameters that are therefore
    observable.
    Can also match model-specific init_reweights to the respective array.
    """
    mapping_matrices = []
    obs_indices = []
    use_internal_scales = defaults["solvers"]["reweight_usescales"]
    if reg_indices:
        reg_diag = []
        if use_internal_scales:
            weights_scaling = []
        if init_reweights is None:
            init_weights = None
        elif isinstance(init_reweights, np.ndarray):
            init_weights = init_reweights
        elif isinstance(init_reweights, dict):
            init_weights = []
    for (mdl_description, model) in models.items():
        if cached_mapping and mdl_description in cached_mapping:
            mapping = cached_mapping[mdl_description].loc[ts.time].values
            observable = np.any(mapping != 0, axis=0)
            if reg_indices and model.regularize:
                nunique = (~np.isclose(np.diff(mapping, axis=0), 0)).sum(axis=0) + 1
                observable = np.logical_and(observable, nunique > 1)
                if isinstance(init_reweights, dict) and (mdl_description in init_reweights):
                    init_weights.append(init_reweights[mdl_description][observable, :])
            mapping = sparse.csc_matrix(mapping[:, observable])
        else:
            mapping, observable = model.get_mapping(ts.time, return_observability=True)
            mapping = mapping[:, observable]
            if reg_indices and model.regularize and \
               isinstance(init_reweights, dict) and (mdl_description in init_reweights):
                init_weights.append(init_reweights[mdl_description][observable, :])
        mapping_matrices.append(mapping)
        obs_indices.append(observable)
        if reg_indices:
            reg_diag.extend([model.regularize] * int(observable.sum()))
            if use_internal_scales and model.regularize:
                weights_scaling.append(getattr(model, "internal_scales",
                                               np.ones(model.num_parameters))[observable])
    G = sparse.hstack(mapping_matrices, format='csc')
    obs_indices = np.concatenate(obs_indices)
    num_time, num_params = G.shape
    assert num_params > 0, f"Mapping matrix is empty, has shape {G.shape}."
    num_comps = ts.num_components
    if reg_indices:
        reg_diag = np.array(reg_diag)
        num_reg = reg_diag.sum()
        if use_internal_scales:
            weights_scaling = np.concatenate(weights_scaling)
        else:
            weights_scaling = None
        if num_reg == 0:
            warn("Regularized solver got no models to regularize.")
        if init_weights is not None:
            if isinstance(init_weights, list):
                init_weights = np.concatenate(init_weights)
            assert init_weights.shape == (num_reg, num_comps), \
                "The combined 'init_reweights' must have the shape " + \
                f"{(num_reg, num_comps)}, got {init_weights.shape}."
        return G, obs_indices, num_time, num_params, num_comps, num_reg, \
            reg_diag, init_weights, weights_scaling
    else:
        return G, obs_indices, num_time, num_params, num_comps


def _build_LS(ts, G, icomp=None, return_W_G=False, use_data_var=True, use_data_cov=True):
    """
    Quick helper function that given a multi-component data vector in ts,
    broadcasts the per-component matrices G and W to joint G and W matrices,
    and then computes GtWG and GtWd, the matrices necessary for least squares.
    If icomp is the index of a single component, only build the GtWG and GtWd
    matrices for that component (ignoring covariances).
    """
    num_comps = ts.num_components
    if icomp is not None:
        assert isinstance(icomp, int) and icomp in list(range(num_comps)), \
            "'icomp' must be a valid integer component index (between 0 and " \
            f"{num_comps-1}), got {icomp}."
        # d and G are dense
        d = ts.df[ts.data_cols[icomp]].values.reshape(-1, 1)
        dnotnan = ~np.isnan(d).squeeze()
        Gout = G.A[dnotnan, :]
        # W is sparse
        if (ts.var_cols is not None) and use_data_var:
            W = sparse.diags(1/ts.df[ts.var_cols[icomp]].values[dnotnan])
        else:
            W = sparse.eye(dnotnan.sum())
    else:
        # d is dense, G and W are sparse
        d = ts.data.values.reshape(-1, 1)
        dnotnan = ~np.isnan(d).squeeze()
        Gout = sparse.kron(G, sparse.eye(num_comps), format='csr')
        if dnotnan.sum() < dnotnan.size:
            Gout = Gout[dnotnan, :]
        if (ts.cov_cols is not None) and use_data_var and use_data_cov:
            Wblocks = [np.linalg.inv(np.reshape(ts.var_cov.values[iobs, ts.var_cov_map],
                                                (num_comps, num_comps)))
                       for iobs in range(ts.num_observations)]
            offsets = list(range(-num_comps, num_comps + 1))
            diags = [np.concatenate([np.concatenate([np.diag(Welem, k), np.zeros(np.abs(k))])
                                     for Welem in Wblocks]) for k in offsets]
            Wn = len(Wblocks) * num_comps
            W = sparse.diags(diags, offsets, shape=(Wn, Wn), format='csr')
            W.eliminate_zeros()
            if dnotnan.sum() < dnotnan.size:
                W = W[dnotnan, :].tocsc()[:, dnotnan]
        elif (ts.var_cols is not None) and use_data_var:
            W = sparse.diags(1/ts.vars.values.reshape(-1, 1))
        else:
            W = sparse.eye(dnotnan.sum())
    if dnotnan.sum() < dnotnan.size:
        # double-check
        if np.any(np.isnan(Gout.data)) or np.any(np.isnan(W.data)):
            raise ValueError("Still NaNs in G or W, unexpected error!")
    # everything here will be dense, except GtW when using data covariance
    d = d[dnotnan]
    GtW = Gout.T @ W
    GtWG = GtW @ Gout
    GtWd = (GtW @ d).squeeze()
    if isinstance(GtWG, sparse.spmatrix):
        GtWG = GtWG.A
    if return_W_G:
        return Gout, W, GtWG, GtWd
    else:
        return GtWG, GtWd


def _pack_params_var(models, params, var, obs_indices, weights=None, reg_diag=None):
    """
    Quick helper function that distributes the parameters (and variances)
    of the input matrices into the respective models.
    obs_indices indicates whether only certain columns (parameters) were estimated.
    If weights and reg_diag are passed, the weights of the regularized model
    parameters are also distributed and returned.
    """
    ix_model = 0
    ix_sol = 0
    num_components = params.shape[1]
    model_params_var = {}
    pack_weights = True if (weights is not None) and (reg_diag is not None) else False
    if pack_weights:
        model_weights = {}
        ix_reg = 0
        assert reg_diag.size == params.shape[0], \
            f"Unexpected parameter size mismatch: {reg_diag.size} != {params.shape}[0]"
    for (mdl_description, model) in models.items():
        mask = obs_indices[ix_model:ix_model+model.num_parameters]
        num_solved = mask.sum()
        p = np.zeros((model.num_parameters, num_components))
        p[mask, :] = params[ix_sol:ix_sol+num_solved, :]
        if var is None:
            v = None
        else:
            v = np.zeros((model.num_parameters, num_components))
            v[mask, :] = var[ix_sol:ix_sol+num_solved, :]
        model_params_var[mdl_description] = (p, v)
        if pack_weights:
            mask_reg = reg_diag[ix_sol:ix_sol+num_solved]
            num_solved_reg = mask_reg.sum()
            if num_solved_reg > 0:
                w = np.zeros((model.num_parameters, num_components))
                w[np.flatnonzero(mask)[mask_reg], :] = weights[ix_reg:ix_reg+num_solved_reg, :]
                model_weights[mdl_description] = w
            else:
                model_weights[mdl_description] = None
            ix_reg += num_solved_reg
        ix_model += model.num_parameters
        ix_sol += num_solved
    assert ix_model == obs_indices.shape[0], \
        f"Unexpected model size mismatch: {ix_model} != {obs_indices.shape}[0]"
    assert ix_sol == params.shape[0], \
        f"Unexpected solution size mismatch: {ix_sol} != {params.shape}[0]"
    if pack_weights:
        assert ix_reg == weights.shape[0], \
            f"Unexpected regularization size mismatch: {ix_reg} != {weights.shape}[0]"
        return model_params_var, model_weights
    else:
        return model_params_var


def _get_reweighting_function():
    """
    Collection of reweighting functions that can be used by lasso_regression.
    """
    name = defaults["solvers"]["reweight_func"]
    eps = defaults["solvers"]["reweight_eps"]
    if name == "inv":
        def rw_func(x):
            return 1/(np.abs(x) + eps)
    elif name == "invsq":
        def rw_func(x):
            return 1/(x**2 + eps**2)
    elif name == "log":
        def rw_func(x):
            mags = np.abs(x)
            return np.log((mags.sum() + np.asarray(x).size * eps) / (mags + eps))
    else:
        raise NotImplementedError(f"'{name}' is an unrecognized reweighting function.")
    return rw_func


def linear_regression(ts, models, formal_variance=False, cached_mapping=None,
                      use_data_variance=True, use_data_covariance=True):
    r"""
    Performs linear, unregularized least squares using :mod:`~scipy.sparse.linalg`.

    The timeseries are the observations :math:`\mathbf{d}`, and the models' mapping
    matrices are stacked together to form a single, sparse mapping matrix
    :math:`\mathbf{G}`. The solver then computes the model parameters
    :math:`\mathbf{m}` that minimize the cost function

    .. math:: f(\mathbf{m}) = \left\| \mathbf{Gm} - \mathbf{d} \right\|_2^2

    where :math:`\mathbf{\epsilon} = \mathbf{Gm} - \mathbf{d}` is the residual.

    If the observations :math:`\mathbf{d}` include a covariance matrix
    :math:`\mathbf{C}_d` (incorporating `var_cols` and possibly also `cov_cols`),
    this data will be used. In this case, :math:`\mathbf{G}` and :math:`\mathbf{d}`
    are replaced by their weighted versions

    .. math:: \mathbf{G} \rightarrow \mathbf{G}^T \mathbf{C}_d^{-1} \mathbf{G}

    and

    .. math:: \mathbf{d} \rightarrow \mathbf{G}^T \mathbf{C}_d^{-1} \mathbf{d}

    The formal model covariance is defined as the pseudo-inverse

    .. math:: \mathbf{C}_m = \left( \mathbf{G}^T \mathbf{C}_d \mathbf{G} \right)^g

    Parameters
    ----------
    ts : geonat.timeseries.Timeseries
        Timeseries to fit.
    models : dict
        Dictionary of :class:`~geonat.models.Model` instances used for fitting.
    formal_variance : bool, optional
        If ``True``, also calculate the formal variance (diagonals of the covariance
        matrix).
    cached_mapping : dict, optional
        If passed, a dictionary containing the mapping matrices as Pandas DataFrames
        for a subset of models and for all timestamps present in ``ts``.
        Mapping matrices not in ``cached_mapping`` will have to be recalculated.
    use_data_variance : bool, optional
        If ``True`` (default) and ``ts`` contains variance information, this
        uncertainty information will be used.
    use_data_covariance : bool, optional
        If ``True`` (default), ``ts`` contains variance and covariance information, and
        ``use_data_variance`` is also ``True``, this uncertainty information will be used.

    Returns
    -------
    model_params_var : dict
        Dictionary of form ``{"model_description": (parameters, variance), ...}``
        which for every model that was fitted, contains a tuple of the best-fit
        parameters and the formal variance (or ``None``, if not calculated).
    """

    # get mapping matrix and sizes
    G, obs_indices, num_time, num_params, num_comps = \
        _combine_mappings(ts, models, cached_mapping=cached_mapping)

    # perform fit and estimate formal covariance (uncertainty) of parameters
    # if there is no covariance, it's num_comps independent problems
    if (ts.cov_cols is None) or (not use_data_covariance):
        params = np.zeros((num_params, num_comps))
        if formal_variance:
            var = np.zeros((num_params, num_comps))
        for i in range(num_comps):
            GtWG, GtWd = _build_LS(ts, G, icomp=i, use_data_var=use_data_variance)
            params[:, i] = sp.linalg.lstsq(GtWG, GtWd)[0].squeeze()
            if formal_variance:
                var[:, i] = np.diag(np.linalg.pinv(GtWG))
    else:
        GtWG, GtWd = _build_LS(ts, G, use_data_var=use_data_variance,
                               use_data_cov=use_data_covariance)
        params = sp.linalg.lstsq(GtWG, GtWd)[0].reshape(num_params, num_comps)
        if formal_variance:
            var = np.diag(np.linalg.pinv(GtWG)).reshape(num_params, num_comps)

    # separate parameters back to models
    model_params_var = _pack_params_var(models, params, var if formal_variance else None,
                                        obs_indices)
    return model_params_var


def ridge_regression(ts, models, penalty, formal_variance=False, cached_mapping=None,
                     use_data_variance=True, use_data_covariance=True):
    r"""
    Performs linear, L2-regularized least squares using :mod:`~scipy.sparse.linalg`.

    The timeseries are the observations :math:`\mathbf{d}`, and the models' mapping
    matrices are stacked together to form a single, sparse mapping matrix
    :math:`\mathbf{G}`. Given the penalty hyperparameter :math:`\lambda`, the solver then
    computes the model parameters :math:`\mathbf{m}` that minimize the cost function

    .. math:: f(\mathbf{m}) = \left\| \mathbf{Gm} - \mathbf{d} \right\|_2^2
              + \lambda \left\| \mathbf{m}_\text{reg} \right\|_2^2

    where :math:`\mathbf{\epsilon} = \mathbf{Gm} - \mathbf{d}` is the residual
    and the subscript :math:`_\text{reg}` masks to zero the model parameters
    not designated to be regularized (see :attr:`~geonat.models.Model.regularize`).

    If the observations :math:`\mathbf{d}` include a covariance matrix
    :math:`\mathbf{C}_d` (incorporating `var_cols` and possibly also `cov_cols`),
    this data will be used. In this case, :math:`\mathbf{G}` and :math:`\mathbf{d}`
    are replaced by their weighted versions

    .. math:: \mathbf{G} \rightarrow \mathbf{G}^T \mathbf{C}_d^{-1} \mathbf{G}

    and

    .. math:: \mathbf{d} \rightarrow \mathbf{G}^T \mathbf{C}_d^{-1} \mathbf{d}

    The formal model covariance is defined as the pseudo-inverse

    .. math:: \mathbf{C}_m = \left( \mathbf{G}^T \mathbf{C}_d \mathbf{G}
                                    + \lambda \mathbf{I}_\text{reg} \right)^g

    where the subscript :math:`_\text{reg}` masks to zero the entries corresponding
    to non-regularized model parameters.

    Parameters
    ----------
    ts : geonat.timeseries.Timeseries
        Timeseries to fit.
    models : dict
        Dictionary of :class:`~geonat.models.Model` instances used for fitting.
    penalty : float
        Penalty hyperparameter :math:`\lambda`.
    formal_variance : bool, optional
        If ``True``, also calculate the formal variance (diagonals of the covariance
        matrix).
    cached_mapping : dict, optional
        If passed, a dictionary containing the mapping matrices as Pandas DataFrames
        for a subset of models and for all timestamps present in ``ts``.
        Mapping matrices not in ``cached_mapping`` will have to be recalculated.
    use_data_variance : bool, optional
        If ``True`` (default) and ``ts`` contains variance information, this
        uncertainty information will be used.
    use_data_covariance : bool, optional
        If ``True`` (default), ``ts`` contains variance and covariance information, and
        ``use_data_variance`` is also ``True``, this uncertainty information will be used.

    Returns
    -------
    model_params_var : dict
        Dictionary of form ``{"model_description": (parameters, variance), ...}``
        which for every model that was fitted, contains a tuple of the best-fit
        parameters and the formal variance (or ``None``, if not calculated).
    """
    if penalty == 0.0:
        warn(f"Ridge Regression (L2-regularized) solver got a penalty of {penalty}, "
             "which effectively removes the regularization.")

    # get mapping and regularization matrix and sizes
    G, obs_indices, num_time, num_params, num_comps, num_reg, reg_diag, _ = \
        _combine_mappings(ts, models, reg_indices=True, cached_mapping=cached_mapping)

    # perform fit and estimate formal covariance (uncertainty) of parameters
    # if there is no covariance, it's num_comps independent problems
    if (ts.cov_cols is None) or (not use_data_covariance):
        reg = np.diag(reg_diag) * penalty
        params = np.zeros((num_params, num_comps))
        if formal_variance:
            var = np.zeros((num_params, num_comps))
        for i in range(num_comps):
            GtWG, GtWd = _build_LS(ts, G, icomp=i, use_data_var=use_data_variance)
            GtWGreg = GtWG + reg
            params[:, i] = sp.linalg.lstsq(GtWGreg, GtWd)[0].squeeze()
            if formal_variance:
                var[:, i] = np.diag(np.linalg.pinv(GtWGreg))
    else:
        GtWG, GtWd = _build_LS(ts, G, use_data_var=use_data_variance,
                               use_data_cov=use_data_covariance)
        reg = np.diag(np.repeat(reg_diag, num_comps)) * penalty
        GtWGreg = GtWG + reg
        params = sp.linalg.lstsq(GtWGreg, GtWd)[0].reshape(num_params, num_comps)
        if formal_variance:
            var = np.diag(np.linalg.pinv(GtWGreg)).reshape(num_params, num_comps)

    # separate parameters back to models
    model_params_var = _pack_params_var(models, params, var if formal_variance else None,
                                        obs_indices)
    return model_params_var


def lasso_regression(ts, models, penalty, reweight_max_iters=None, reweight_max_rss=1e-10,
                     init_reweights=None, reweights_coupled=True, formal_variance=False,
                     cached_mapping=None, use_data_variance=True, use_data_covariance=True,
                     return_weights=False, cvxpy_kw_args={}):
    r"""
    Performs linear, L1-regularized least squares using
    `CVXPY <https://www.cvxpy.org/index.html>`_.

    The timeseries are the observations :math:`\mathbf{d}`, and the models' mapping
    matrices are stacked together to form a single, sparse mapping matrix
    :math:`\mathbf{G}`. Given the penalty hyperparameter :math:`\lambda`, the solver then
    computes the model parameters :math:`\mathbf{m}` that minimize the cost function

    .. math:: f(\mathbf{m}) = \left\| \mathbf{Gm} - \mathbf{d} \right\|_2^2
              + \lambda \left\| \mathbf{m}_\text{reg} \right\|_1

    where :math:`\mathbf{\epsilon} = \mathbf{Gm} - \mathbf{d}` is the residual
    and the subscript :math:`_\text{reg}` masks to zero the model parameters
    not designated to be regularized (see :attr:`~geonat.models.Model.regularize`).

    If the observations :math:`\mathbf{d}` include a covariance matrix
    :math:`\mathbf{C}_d` (incorporating `var_cols` and possibly also `cov_cols`),
    this data will be used. In this case, :math:`\mathbf{G}` and :math:`\mathbf{d}`
    are replaced by their weighted versions

    .. math:: \mathbf{G} \rightarrow \mathbf{G}^T \mathbf{C}_d^{-1} \mathbf{G}

    and

    .. math:: \mathbf{d} \rightarrow \mathbf{G}^T \mathbf{C}_d^{-1} \mathbf{d}

    If ``reweight_max_iters`` is specified, sparsity of the solution parameters is promoted
    by iteratively reweighting the penalty parameter for each regularized parameter based
    on its current value, approximating the L0 norm rather than the L1 norm (see Notes).

    The formal model covariance :math:`\mathbf{C}_m` is defined as being zero except in
    the rows and columns corresponding to non-zero parameters, where it is defined
    exactly as the unregularized version (see :func:`~geonat.solvers.linear_regression`),
    restricted to those same rows and columns.

    Parameters
    ----------
    ts : geonat.timeseries.Timeseries
        Timeseries to fit.
    models : dict
        Dictionary of :class:`~geonat.models.Model` instances used for fitting.
    penalty : float
        Penalty hyperparameter :math:`\lambda`.
    reweight_max_iters : int, optional
        If an integer, number of solver iterations (see Notes), resulting in reweighting.
        Defaults to no reweighting (``None``).
    reweight_max_rss : float, optional
        When reweighting is active and the maximum number of iterations has not yet
        been reached, let the iteration stop early if the solutions do not change much
        anymore (see Notes).
        Defaults to ``1e-10``. Set to ``0`` to deactivate eraly stopping.
    init_reweights : numpy.ndarray, optional
        When reweighting is active, use this array to initialize the weights.
        It has to have size :math:`\text{num_components} \cdot \text{num_reg}`, where
        :math:`\text{num_components}=1` if covariances are not used (and the actual
        number of timeseries components otherwise) and :math:`\text{num_reg}` is the
        number of regularized model parameters.
    reweights_coupled : bool, optional
        If ``True`` (default) and reweighting is active, the L1 penalty hyperparameter
        is coupled with the reweighting weights (see Notes).
    formal_variance : bool, optional
        If ``True``, also calculate the formal variance (diagonals of the covariance
        matrix).
    cached_mapping : dict, optional
        If passed, a dictionary containing the mapping matrices as Pandas DataFrames
        for a subset of models and for all timestamps present in ``ts``.
        Mapping matrices not in ``cached_mapping`` will have to be recalculated.
    use_data_variance : bool, optional
        If ``True`` (default) and ``ts`` contains variance information, this
        uncertainty information will be used.
    use_data_covariance : bool, optional
        If ``True`` (default), ``ts`` contains variance and covariance information, and
        ``use_data_variance`` is also ``True``, this uncertainty information will be used.
    return_weights : bool, optional
        When reweighting is active, set to ``True`` to return the weights after the last
        update.
        Defaults to ``False``.
    cvxpy_kw_args : dict
        Additional keyword arguments passed on to CVXPY's ``solve()`` function.

    Returns
    -------
    model_params_var : dict
        Dictionary of form ``{"model_description": (parameters, variance), ...}``
        which for every model that was fitted, contains a tuple of the best-fit
        parameters and the formal variance (or ``None``, if not calculated).

    Notes
    -----

    The L0-regularization approximation used by setting ``reweight_max_iters >= 0`` is based
    on [candes08]_. The idea here is to iteratively reduce the cost (before multiplication
    with :math:`\lambda`) of regularized, but significant parameters to 1, and iteratively
    increasing the cost of a regularized, but small parameter to a much larger value.

    This is achieved by introducing an additional parameter vector :math:`\mathbf{w}`
    of the same shape as the regularized parameters, inserting it into the L1 cost,
    and iterating between solving the L1-regularized problem, and using a reweighting
    function on those weights:

    1.  Initialize :math:`\mathbf{w}^{(0)} = \mathbf{1}`
        (or use the array from ``init_reweights``).
    2.  Solve the modified weighted L1-regularized problem minimizing
        :math:`f(\mathbf{m}^{(i)}) = \left\| \mathbf{Gm}^{(i)} -
        \mathbf{d} \right\|_2^2 + \lambda \left\| \mathbf{w}^{(i)} \circ
        \mathbf{m}^{(i)}_\text{reg} \right\|_1`
        where :math:`\circ` is the element-wise multiplication and :math:`i` is
        the iteration step.
    3.  Update the weights element-wise using a predefined reweighting function
        :math:`\mathbf{w}^{(i+1)} = w(\mathbf{m}^{(i)}_\text{reg})`.
    4.  Repeat from step 2 until ``reweight_max_iters`` iterations are reached
        or the root sum of squares of the difference between the last and current
        solution is less than ``reweight_max_rss``.

    The reweighting function is set in the :attr:`~geonat.config.defaults` dictionary
    using the ``reweight_func`` key (along with a stabilizing parameter
    ``reweight_eps`` that should not need tuning). It defaults to the logarithmic
    reweighting function. Possible values are:

    +--------------+------------------------------------------------------------------------------+
    | ``'log'``    | :math:`w(m_j) = \log\frac{\sum_j\|m_j\|\cdot\text{eps}}{\|m_j\|+\text{eps}}` |
    +--------------+------------------------------------------------------------------------------+
    | ``'inv'``    | :math:`w(m_j) = \frac{1}{\|m_j\| + \text{eps}}`                              |
    +--------------+------------------------------------------------------------------------------+
    | ``'inv_sq'`` | :math:`w(m_j) = \frac{1}{m_j^2 + \text{eps}^2}`                              |
    +--------------+------------------------------------------------------------------------------+

    If reweighting is active and ``reweights_coupled=True``, :math:`\lambda`
    is moved into the norm and combined with :math:`\mathbf{w}`, such that
    the reweighting applies to the product of both.
    Note that if ``init_reweights`` is not ``None``, then the ``penalty`` is ignored
    since it should already be contained in the passed weights array.

    References
    ----------
    .. [candes08] Candès, E. J., Wakin, M. B., & Boyd, S. P. (2008).
       *Enhancing Sparsity by Reweighted ℓ1 Minimization.*
       Journal of Fourier Analysis and Applications, 14(5), 877–905.
       doi:`10.1007/s00041-008-9045-x <https://doi.org/10.1007/s00041-008-9045-x>`_.
    """
    if penalty == 0:
        warn(f"Lasso Regression (L1-regularized) solver got a penalty of {penalty}, "
             "which removes the regularization.")

    # get mapping and regularization matrix
    G, obs_indices, num_time, num_params, num_comps, num_reg, \
        reg_diag, init_weights, weights_scaling = \
        _combine_mappings(ts, models, reg_indices=True, cached_mapping=cached_mapping,
                          init_reweights=init_reweights)
    regularize = (num_reg > 0) and (penalty > 0)
    if (not regularize) or (reweight_max_iters is None):
        return_weights = False
    if reweight_max_iters is None:
        n_iters = 1
    else:
        assert isinstance(reweight_max_iters, int) and reweight_max_iters > 0
        n_iters = int(reweight_max_iters)

    # solve CVXPY problem while checking for convergence
    def solve_problem(GtWG, GtWd, reg_diag, num_comps=num_comps, init_weights=init_weights):
        # build objective function
        m = cp.Variable(GtWd.size)
        objective = cp.norm2(GtWG @ m - GtWd)
        constraints = None
        if regularize:
            lambd = cp.Parameter(value=penalty, pos=True)
            if reweight_max_iters is not None:
                rw_func = _get_reweighting_function()
                reweight_size = num_reg*num_comps
                if init_weights is None:
                    init_weights = np.ones(reweight_size)
                    if reweights_coupled:
                        init_weights *= penalty
                else:
                    assert init_weights.size == reweight_size, \
                        f"'init_weights' must have a size of {reweight_size}, " + \
                        f"got {init_reweights.size}."
                weights = cp.Parameter(shape=reweight_size,
                                       value=init_weights, pos=True)
                z = cp.Variable(shape=reweight_size)
                if reweights_coupled:
                    objective = objective + cp.norm1(z)
                else:
                    objective = objective + lambd * cp.norm1(z)
                constraints = [z == cp.multiply(weights, m[reg_diag])]
                old_m = np.zeros(m.shape)
            else:
                objective = objective + lambd * cp.norm1(m[reg_diag])
        # define problem
        problem = cp.Problem(cp.Minimize(objective), constraints)
        # solve
        for i in range(n_iters):  # always solve at least once
            try:
                problem.solve(enforce_dpp=True, **cvxpy_kw_args)
            except cp.error.SolverError as e:
                # no solution found, but actually a more serious problem
                warn(str(e))
                converged = False
                break
            else:
                if m.value is None:  # no solution found
                    converged = False
                    break
                # solved
                converged = True
                # if iterating, extra tasks
                if regularize and reweight_max_iters is not None:
                    # update weights
                    if weights_scaling is not None:
                        weights.value = rw_func(m.value[reg_diag]*weights_scaling)
                    else:
                        weights.value = rw_func(m.value[reg_diag])
                    # check if the solution changed to previous iteration
                    if (i > 0) and (np.sqrt(np.sum((old_m - m.value)**2)) < reweight_max_rss):
                        break
                    # remember previous solution
                    old_m[:] = m.value[:]
        # return
        if converged and (regularize and reweight_max_iters is not None):
            result = (m.value, weights.value)
        elif converged:
            result = (m.value, None)
        else:
            result = (None, None)
        return result

    # perform fit and estimate formal covariance (uncertainty) of parameters
    # if there is no covariance, it's num_comps independent problems
    if (ts.cov_cols is None) or (not use_data_covariance):
        # initialize output
        params = np.zeros((num_params, num_comps))
        if formal_variance:
            var = np.zeros((num_params, num_comps))
        if regularize and return_weights:
            weights = np.zeros((num_reg, num_comps))
        # loop over components
        for i in range(num_comps):
            # build and solve problem
            Gnonan, Wnonan, GtWG, GtWd = _build_LS(ts, G, icomp=i, return_W_G=True,
                                                   use_data_var=use_data_variance)
            solution, wts = solve_problem(GtWG, GtWd, reg_diag, num_comps=1,
                                          init_weights=init_weights[:, i]
                                          if init_weights is not None else None)
            # store results
            if solution is None:
                params[:, i] = np.NaN
                if formal_variance:
                    var[:, i] = np.NaN
                if regularize and return_weights:
                    weights[:, i] = np.NaN
            else:
                params[:, i] = solution
                # if desired, estimate formal variance here
                if formal_variance:
                    best_ind = np.nonzero(solution)
                    Gsub = Gnonan[:, best_ind]
                    GtWG = Gsub.T @ Wnonan @ Gsub
                    var[best_ind, i] = np.diag(np.linalg.pinv(GtWG))
                if regularize and return_weights:
                    weights[:, i] = wts
    else:
        # build stacked problem and solve
        Gnonan, Wnonan, GtWG, GtWd = _build_LS(ts, G, return_W_G=True,
                                               use_data_var=use_data_variance,
                                               use_data_cov=use_data_covariance)
        reg_diag = np.repeat(reg_diag, num_comps)
        solution, weights = solve_problem(GtWG, GtWd, reg_diag)
        # store results
        if solution is None:
            params = np.empty((num_params, num_comps))
            params[:] = np.NaN
            if formal_variance:
                var = np.empty((num_params, num_comps))
                var[:] = np.NaN
            if regularize and return_weights:
                weights = np.empty((num_reg, num_comps))
                weights[:] = np.NaN
        else:
            params = solution.reshape(num_params, num_comps)
            # if desired, estimate formal variance here
            if formal_variance:
                var = np.zeros(num_params * num_comps)
                best_ind = np.nonzero(solution)
                Gsub = Gnonan.tocsc()[:, best_ind]
                GtWG = Gsub.T @ Wnonan @ Gsub
                var[best_ind, :] = np.diag(np.linalg.pinv(GtWG))
                var = var.reshape(num_params, num_comps)
            if regularize and return_weights:
                weights = weights.reshape(num_reg, num_comps)

    # separate parameters back to models and return
    if return_weights:
        model_params_var, model_weights = _pack_params_var(models, params,
                                                           var if formal_variance else None,
                                                           obs_indices, weights, reg_diag)
        return model_params_var, model_weights
    else:
        model_params_var = _pack_params_var(models, params, var if formal_variance else None,
                                            obs_indices)
        return model_params_var,


class SpatialSolver():
    r"""
    Solver class that in combination with :func:`~lasso_regression` solves the
    spatiotemporal, L0-reweighted least squares fitting problem given the models and
    timeseries found in a target :class:`~geonat.network.Network` object.
    This is achieved by following the alternating computation scheme as described
    in :meth:`~solve`.

    Parameters
    ----------
    net : geonat.network.Network
        Network to fit.
    ts_description : str
        Description of the timeseries to fit.
    model_list : list, optional
        List of strings containing the model names of the subset of the models
        to fit. Defaults to all models.
    """
    def __init__(self, net, ts_description, model_list=None):
        self.net = net
        """ Network object to fit. """
        self.ts_description = ts_description
        """ Name of timeseries to fit. """
        self.model_list = model_list
        """ Names of the models to fit (``None`` for all). """

    def solve(self, penalty, spatial_reweight_models, spatial_reweight_iters=5,
              spatial_reweight_percentile=0.5, local_reweight_iters=1,
              reweights_coupled=True, formal_variance=False, use_data_variance=True,
              use_data_covariance=True, cvxpy_kw_args={}):
        r"""
        Solve the network-wide fitting problem as follows:

            1.  Fit the models individually using a single iteration step from
                :func:`~lasso_regression`.
            2.  Collect the L0 weights :math:`\mathbf{w}^{(i)}` from each station.
            3.  Spatially combine (e.g. average) the weights, and redistribute them
                to the stations for the next iteration.
            4.  Repeat from 1.

        Parameters
        ----------
        penalty : float
            Penalty hyperparameter :math:`\lambda`.
        spatial_reweight_models : list
            Names of models to use in the spatial reweighting.
        spatial_reweight_iters : int, optional
            Number of spatial reweighting iterations.
        spatial_reweight_percentile : float, optional
            Percentile used in the spatial reweighting.
            Defaults to ``0.5``.
        local_reweight_iters : int, optional
            Number of local reweighting iterations, see ``reweight_max_iters`` in
            :func:`~lasso_regression`.
        reweights_coupled : bool, optional
            If ``True`` (default) and reweighting is active, the L1 penalty hyperparameter
            is coupled with the reweighting weights (see Notes).
        formal_variance : bool, optional
            If ``True``, also calculate the formal variance (diagonals of the covariance
            matrix).
        use_data_variance : bool, optional
            If ``True`` (default) and ``ts`` contains variance information, this
            uncertainty information will be used.
        use_data_covariance : bool, optional
            If ``True`` (default), ``ts`` contains variance and covariance information, and
            ``use_data_variance`` is also ``True``, this uncertainty information will be used.
        cvxpy_kw_args : dict
            Additional keyword arguments passed on to CVXPY's ``solve()`` function.
        """
        assert isinstance(spatial_reweight_models, list) and \
            all([isinstance(mdl, str) for mdl in spatial_reweight_models]), \
            "'spatial_reweight_models' must be a list of model name strings, got " + \
            f"{spatial_reweight_models}."
        assert isinstance(spatial_reweight_iters, int) and (spatial_reweight_iters >= 0), \
            "'spatial_reweight_iters' must be an integer greater or equal than 0, got " + \
            f"{spatial_reweight_iters}."

        # get scale lengths (correlation lengths)
        # using the average distance to the closest 3 stations
        tqdm.write("Calculating scale lengths")
        geoid = cgeod.Geodesic()
        station_names = list(self.net.stations.keys())
        station_lonlat = np.stack([np.array(self.net[name].location)[[1, 0]]
                                   for name in station_names])
        all_distances = np.empty((self.net.num_stations, self.net.num_stations))
        net_avg_closests = []
        for i, name in enumerate(station_names):
            all_distances[i, :] = np.array(geoid.inverse(station_lonlat[i, :].reshape(1, 2),
                                                         station_lonlat))[:, 0]
            net_avg_closests.append(np.sort(all_distances[i, :])[1:1+4].mean())
        distance_weights = np.exp(-all_distances / np.array(net_avg_closests).reshape(1, -1))

        # first solve, default initial weights
        tqdm.write("Performing initial solve")
        net_weights = self.net.fit(self.ts_description, model_list=self.model_list,
                                   solver="lasso_regression", cached_mapping=True,
                                   penalty=penalty,
                                   reweight_max_iters=local_reweight_iters,
                                   reweights_coupled=reweights_coupled,
                                   return_weights=True,
                                   formal_variance=formal_variance,
                                   use_data_variance=use_data_variance,
                                   use_data_covariance=use_data_covariance,
                                   cvxpy_kw_args=cvxpy_kw_args)

        # iterate the specified amount of times, updating the weights in between
        # TODO: implement early stopping criteria
        max_penalty = _get_reweighting_function()(0)
        for i in range(1, spatial_reweight_iters + 1):
            tqdm.write("Updating weights")
            model_list = list(set([mdl for n_w in net_weights.values() for mdl in n_w[0].keys()]))
            new_net_weights = {statname: {"init_reweights": {}} for statname in station_names}
            num_pweights_before, num_pweights_after = 0, 0
            for mdl_description in model_list:
                mdl_weights_dict = {name: net_weights[name][0][mdl_description]
                                    for name in station_names
                                    if mdl_description in net_weights[name][0]}
                mdl_weights = [mdl for mdl in mdl_weights_dict.values()
                               if isinstance(mdl, np.ndarray)]
                mdl_weights_shapes = [mdl.shape for mdl in mdl_weights]
                if (mdl_description in spatial_reweight_models) and (len(mdl_weights) > 0) and \
                   (mdl_weights_shapes.count(mdl_weights_shapes[0]) == len(mdl_weights)):
                    print(f"Stacking model {mdl_description}")
                    new_net_weights[name]["init_reweights"] = {}
                    parameter_weights = np.stack(mdl_weights)
                    num_pweights_before += (parameter_weights >= max_penalty).sum()
                    for station_index, name in enumerate(station_names):
                        new_weights = weighted_median(parameter_weights,
                                                      distance_weights[station_index, :],
                                                      percentile=spatial_reweight_percentile)
                        num_pweights_after += (new_weights >= max_penalty).sum()
                        new_net_weights[name]["init_reweights"][mdl_description] = new_weights
                else:
                    if mdl_description in spatial_reweight_models:
                        warn(f"{mdl_description} cannot be stacked, got {mdl_weights_dict} " +
                             "(model should have been spatially reweighted).")
                    for name in station_names:
                        if mdl_description in net_weights[name][0]:
                            new_net_weights[name]["init_reweights"][mdl_description] = \
                                net_weights[name][0][mdl_description]
            tqdm.write("Number of maximum penalty weights changed from " +
                       f"{num_pweights_before} to {num_pweights_after}.")
            tqdm.write(f"Solving after {i} reweightings")
            net_weights = self.net.fit(self.ts_description, model_list=self.model_list,
                                       solver="lasso_regression", cached_mapping=True,
                                       local_input=new_net_weights,
                                       penalty=penalty,
                                       reweight_max_iters=local_reweight_iters,
                                       reweights_coupled=reweights_coupled,
                                       return_weights=True,
                                       formal_variance=formal_variance,
                                       use_data_variance=use_data_variance,
                                       use_data_covariance=use_data_covariance,
                                       cvxpy_kw_args=cvxpy_kw_args)
        tqdm.write("Done")
