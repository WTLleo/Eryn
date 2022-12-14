# -*- coding: utf-8 -*-

import warnings

import numpy as np
from itertools import count
from copy import deepcopy

from .backends import Backend, HDFBackend
from .model import Model
from .moves import StretchMove, TemperatureControl, DistributionGenerateRJ, GaussianMove
from .pbar import get_progress_bar
from .state import State
from .prior import ProbDistContainer
from .utils import PlotContainer
from .utils import PeriodicContainer
from .utils.utility import groups_from_inds


__all__ = ["EnsembleSampler", "walkers_independent"]


try:
    from collections.abc import Iterable
except ImportError:
    # for py2.7, will be an Exception in 3.8
    from collections import Iterable


class EnsembleSampler(object):
    """An ensemble MCMC sampler

    The class controls the entire sampling run. It can handle
    everything from a basic non-tempered MCMC to a parallel-tempered,
    global fit containing multiple branches (models) and a variable
    number of leaves (sources) per branch. (# TODO: add link to tree explainer)
  
    Parameters related to parallelization can be controlled via the ``pool`` argument.

    Args:
        nwalkers (int): The number of walkers in the ensemble per temperature.
        ndims (int or list of ints): Number of dimensions in the parameter space
            for each branch tested.
        log_like_fn (callable): A function that returns the natural logarithm of the
            likelihood for that position. The inputs to ``log_like_fn`` depend on whether
            the function is vectorized (kwarg ``vectorize`` below), if you are using reversible jump, 
            and how many branches you have. 
            
                In the simplest case where ``vectorize == False``, no reversible jump, and only one 
                type of model, the inputs are just the array of parameters for one walker, so shape is ``(ndim,)``.

                If ``vectorize == True``, no reversible jimp, and only one type of model, the inputs will 
                be a 2D array of parameters of all the walkers going in. Shape: ``(num positions, ndim)``.

                If using reversible jump, the leaves that go together in the same Likelihood will be grouped
                together into a single function call. If ``vectorize == False``, then each group is sent as
                an individual computation. With ``N`` different branches (``N > 1``), inputs would be a list 
                of 2D arrays of the coordinates for all leaves within each branch: ``([x0, x1,...,xN])``
                where ``xi`` is 2D with shape ``(number of leaves in this branch, ndim)``. If ``N == 1``, then
                a list is not provided, just x0, the 2D array of coordinates for the one branch considered.

                If using reversible jump and ``vectorize == True``, then the arrays of parameters will be output
                with information as regards the grouping of branch and leaf set. Inputs will be
                ``([X0, X1,..XN], [group0, group1,...,groupN])`` where ``Xi`` is a 2D array of all
                leaves in the sampler for branch ``i``. ``groupi`` is an index indicating which unique group
                that sources belongs. For example, if we have 3 walkers with (1, 2, 1) leaves for model ``i``,
                respectively, we wil have an ``Xi = array([params0, params1, params2, params3])`` and 
                ``groupsi = array([0, 1, 1, 2])``. 
                If ``N == 1``, then the lists are removed and the inputs become ``(X0, group0)``. 

                Extra ``args`` and ``kwargs`` for the Likelihood function can be added with the kwargs 
                ``args`` and ``kwargs`` below.

                Please see the tutorial for more information. (# TODO: add link to tutorial)           
        priors (dict): The prior dictionary can take four forms.
            1) A dictionary with keys as int or tuple containing the int or tuple of int
            that describe the parameter number over which to assess the prior, and values that
            are prior probability distributions that must have a ``logpdf`` class method.
            2) A :class:`eryn.prior.ProbDistContainer` object.
            3) A dictionary with keys that are ``branch_names`` and values that are dictionaries for
            each branch as described for (1).
            4) A dictionary with keys that are ``branch_names`` and values are
            :class:`eryn.prior.ProbDistContainer` objects.
        provide_groups (bool, optional): If ``True``, provide groups as described in ``log_like_fn`` above.
            A group parameter is added for each branch. (default: ``False``)
        provide_supplimental (bool, optional): If ``True``, it will provide keyword arguments to 
            the Likelihood function: ``supps`` and ``branch_supps``. Please see the Tutorial
            (# TODO: add tutorial link) and :class:`eryn.state.BranchSupplimental` for more information.
        tempering_kwargs (dict, optional): Keyword arguments for initialization of the
            tempering class: :class:`eryn.moves.tempering.TemperatureControl`.  (default: ``{}``)
        branch_names (list, optional): List of branch names. If ``None``, models will be assigned
            names as ``f"model_{index}"``. (default: ``None``)
        nbranches (int, optional): Number of branches (models) tested. (default: ``1``)
        nleaves_max (int, list of int, or int np.ndarray[nbranches], optional):
            Number of maximum allowable leaves for each branch. (default: ``1``)
        nleaves_min (int, list of int, or int np.ndarray[nbranches], optional):
            Number of minimum allowable leaves for each branch. This is only
            used when using reversible jump. (default: ``1``)
        pool (object, optional): An object with a ``map`` method that follows the same
            calling sequence as the built-in ``map`` function. This is
            generally used to compute the log-probabilities for the ensemble
            in parallel.
        moves (list or object, optional): This can be a single move object, a list of moves,
            or a "weighted" list of the form ``[(eryn.moves.StretchMove(),
            0.1), ...]``. When running, the sampler will randomly select a
            move from this list (optionally with weights) for each proposal.
            If ``None``, the default will be :class:`StretchMove`.
            (default: ``None``)
        rj_moves (list or object, optional): If ``None`` or ``False``, reversible jump will not be included in the run.
            This can be a single move object, a list of moves,
            or a "weighted" list of the form ``[(eryn.moves.DistributionGenerateRJ(),
            0.1), ...]``. When running, the sampler will randomly select a
            move from this list (optionally with weights) for each proposal.
            If ``True``, it defaults to :class:`DistributionGenerateRJ`.
            (default: ``None``)
        dr_moves (bool, optional): If ``None`` ot ``False``, delayed rejection when proposing "birth"
            of new components/models will be switched off for this run. Requires ``rj_moves`` set to ``True``.
            # TODO: check in with Nikos about this
            (default: ``None``)
        dr_max_iter (int, optional): Maximum number of iterations used with delayed rejection. (default: 5)
        args (optional): A list of extra positional arguments for
            ``log_like_fn``. ``log_like_fn`` will be called as
            ``log_like_fn(sampler added args, *args, sampler added kwargs, **kwargs)``.
        kwargs (optional): A dict of extra keyword arguments for
            ``log_like_fn``. ``log_like_fn`` will be called as
            ``log_like_fn(sampler added args, *args, sampler added kwargs, **kwargs)``.
        backend (optional): Either a :class:`backends.Backend` or a subclass
            (like :class:`backends.HDFBackend`) that is used to store and
            serialize the state of the chain. By default, the chain is stored
            as a set of numpy arrays in memory, but new backends can be
            written to support other mediums.
        vectorize (bool, optional): If ``True``, ``log_like_fn`` is expected
            to accept an array of position vectors instead of just one. Note
            that ``pool`` will be ignored if this is ``True``. See ``log_like_fn`` information
            above to understand the arguments of ``log_like_fn`` based on whether 
            ``vectorize`` is ``True``. 
            (default: ``False``)
        plot_iterations (int, optional): If ``plot_iterations == -1``, then the
            diagnostic plots will not be constructed. Otherwise, the diagnostic
            plots will be constructed every ``plot_iterations`` sampler iterations.
            (default: -1)
        plot_generator (optional): # TODO: add class object that controls
            the diagnostic plotting updates. If not provided and ``plot_iterations > 0``,
            the ensemble will initialize a default plotting setup.
            (default: None)
        plot_name (str, optional): Name of file to save diagnostic plots to. This only
            applies if ``plot_generator == None`` and ``plot_iterations > 0``.
            (default: ``None``)
        periodic (dict, optional): Keys are ``branch_names``. Values are dictionaries
            that have (key: value) pairs as (index to parameter: period). Periodic
            parameters are treated as having periodic boundary conditions in proposals.
        update_fn (callable, optional): :class:`eryn.utils.updates.AdjustStretchProposalScale`
            object that allows the user to update the sampler in any preferred way
            every ``update_iterations`` sampler iterations. The callable must have signature:
            ``(sampler iteration, last sample state object, EnsembleSampler object)``. 
        update_iterations (int, optional): Number of iterations between sampler
            updates using ``update_fn``. Updates are only performed at the thinning rate. 
            If ``thin_by>1`` when :func:`EnsembleSampler.run_mcmc` is used, the sampler
            is updated every ``thin_by * update_iterations`` iterations. 
        stopping_fn (callable, optional): :class:`eryn.utils.stopping.Stopping` object that
            allows the user to end the sampler if specified criteria are met.
            The callable must have signature:
            ``(sampler iteration, last sample state object, EnsembleSampler object)``. 
        stopping_iterations (int, optional): Number of iterations between sampler
            attempts to evaluate the ``stopping_fn``. Stopping checks are only performed at the thinning rate. 
            If ``thin_by>1`` when :func:`EnsembleSampler.run_mcmc` is used, the sampler
            is checked for the stopping criterion every ``thin_by * stopping_iterations`` iterations.
        fill_zero_leaves_val (double, optional): When there are zero leaves in a
            given walker (across all branches), fill the likelihood value with
            ``fill_zero_leaves_val``. If wanting to keep zero leaves as a possible
            model, this should be set to the value of the contribution to the Likelihood
            from the data. (Default: ``-1e300``).
        num_repeats_in_model (int, optional): Number of times to repeat the in-model step
            within in one sampler iteration. When analyzing the acceptance fraction, you must 
            include the value of ``num_repeats_in_model`` to get the proper denominator.
        num_repeats_rj (int, optional): Number of time to repeat the reversible jump step 
            within in one sampler iteration. When analyzing the acceptance fraction, you must 
            include the value of ``num_repeats_rj`` to get the proper denominator.
        verbose (int, optional): # TODO
        info (dict, optional): Key and value pairs reprenting any information
            the user wants to add to the backend if the user is not inputing
            their own backend.

    Raises:
        ValueError: Any startup issues.

    """

    def __init__(
        self,
        nwalkers,
        ndims,  # assumes ndim_max
        log_like_fn,
        priors,
        provide_groups=False,  # TODO: improve this
        provide_supplimental=False,  # TODO: improve this
        tempering_kwargs={},
        branch_names=None,
        nbranches=1,
        nleaves_max=1,
        nleaves_min=1,
        pool=None,
        moves=None,
        rj_moves=None,
        dr_moves=None,
        dr_max_iter=5,
        args=None,
        kwargs=None,
        backend=None,
        vectorize=False,
        blobs_dtype=None,  # TODO check this
        plot_iterations=-1,  # TODO: do plot stuff?
        plot_generator=None,
        plot_name=None,
        periodic=None,
        update_fn=None,
        update_iterations=-1,
        stopping_fn=None,
        stopping_iterations=-1,
        fill_zero_leaves_val=-1e300,
        num_repeats_in_model=1,
        num_repeats_rj=1,
        verbose=False,
        info={},
    ):

        # store priors
        self.priors = priors

        # store some kwargs
        self.provide_groups = provide_groups
        self.provide_supplimental = provide_supplimental
        self.fill_zero_leaves_val = fill_zero_leaves_val
        self.num_repeats_in_model = num_repeats_in_model
        self.num_repeats_rj = num_repeats_rj

        # setup emcee-like basics
        self.pool = pool
        self.vectorize = vectorize
        self.blobs_dtype = blobs_dtype

        # store default branch names if not given
        if branch_names is None:
            branch_names = ["model_{}".format(i) for i in range(nbranches)]

        assert len(branch_names) == nbranches

        # setup dimensions for branches
        # turn things into lists if ints are given
        if isinstance(ndims, int):
            ndims = [ndims for _ in range(nbranches)]
        elif not isinstance(ndims, list):
            raise ValueError("ndims must be integer or list.")

        if isinstance(nleaves_max, int):
            nleaves_max = [nleaves_max]

        # setup temperaing information
        # default is no temperatures
        if tempering_kwargs == {}:
            self.ntemps = 1
            self.temperature_control = None
        else:
            self.temperature_control = TemperatureControl(
                ndims, nwalkers, nleaves_max, **tempering_kwargs
            )
            self.ntemps = self.temperature_control.ntemps

        # set basic variables for sampling settings
        self.ndims = ndims  # interpeted as ndim_max
        self.nwalkers = nwalkers
        self.nbranches = nbranches
        self.nleaves_max = nleaves_max
        self.branch_names = branch_names

        # eryn wraps periodic parameters
        if periodic is not None:
            if not isinstance(periodic, PeriodicContainer) and not isinstance(
                periodic, dict
            ):
                raise ValueError(
                    "periodic must be PeriodicContainer or dict if not None."
                )
            elif isinstance(periodic, dict):
                periodic = PeriodicContainer(periodic)

        # Parse the move schedule
        if moves is None:
            if rj_moves is not None:
                raise ValueError(
                    "If providing rj_moves, must provide moves kwarg as well."
                )

            # defaults to stretch move
            self.moves = [
                StretchMove(
                    temperature_control=self.temperature_control,
                    periodic=periodic,
                    a=2.0,
                )
            ]
            self.weights = [1.0]

        elif isinstance(moves, Iterable):
            try:
                self.moves, self.weights = zip(*moves)
            except TypeError:
                self.moves = moves
                self.weights = np.ones(len(moves))
        else:
            self.moves = [moves]
            self.weights = [1.0]

        self.weights = np.atleast_1d(self.weights).astype(float)
        self.weights /= np.sum(self.weights)

        # parse the reversible jump move schedule
        if rj_moves is None:
            self.has_reversible_jump = False
        elif isinstance(rj_moves, bool):
            self.has_reversible_jump = rj_moves
            # TODO: deal with tuning
            if self.has_reversible_jump:
                if nleaves_min is None:
                    # default to 0 for all models
                    self.nleaves_min = [0 for _ in range(self.nbranches)]
                elif isinstance(nleaves_min, int):
                    self.nleaves_min = [nleaves_min for _ in range(self.nbranches)]
                elif isinstance(nleaves_min, list):
                    self.nleaves_min = nleaves_min
                else:
                    raise ValueError(
                        "If providing a minimum number of leaves, must be int or list of ints."
                    )

                assert len(self.nleaves_min) == self.nbranches

                # default to DistributionGenerateRJ
                rj_move = DistributionGenerateRJ(
                    self.priors,
                    self.nleaves_max,
                    self.nleaves_min,
                    dr=dr_moves,
                    dr_max_iter=dr_max_iter,
                    tune=False,
                    temperature_control=self.temperature_control,
                )
                self.rj_moves = [rj_move]
                self.rj_weights = [1.0]

        # same as above for moves
        elif isinstance(rj_moves, Iterable):
            self.has_reversible_jump = True

            try:
                self.rj_moves, self.rj_weights = zip(*rj_moves)
            except TypeError:
                self.rj_moves = rj_moves
                self.rj_weights = np.ones(len(rj_moves))

        else:
            self.has_reversible_jump = True
            # TODO: fix error catch here
            self.rj_moves = [rj_moves]
            self.rj_weights = [1.0]

        # adjust rj weights properly
        if self.has_reversible_jump:
            self.rj_weights = np.atleast_1d(self.rj_weights).astype(float)
            self.rj_weights /= np.sum(self.rj_weights)

        # make sure moves have temperature module
        if self.temperature_control is not None:
            for move in self.moves:
                if move.temperature_control is None:
                    move.temperature_control = self.temperature_control

            if self.has_reversible_jump:
                for move in self.rj_moves:
                    if move.temperature_control is None:
                        move.temperature_control = self.temperature_control

        # make sure moves have temperature module
        if periodic is not None:
            for move in self.moves:
                if move.periodic is None:
                    move.periodic = periodic

            if self.has_reversible_jump:
                for move in self.rj_moves:
                    if move.periodic is None:
                        move.periodic = periodic

        # prepare the per proposal accepted values that are held as attributes in the specific classes
        for move in self.moves:
            move.accepted = np.zeros((self.ntemps, self.nwalkers))

        if self.has_reversible_jump:
            for move in self.rj_moves:
                move.accepted = np.zeros((self.ntemps, self.nwalkers))

        # setup backend if not provided or initialized
        if backend is None:
            self.backend = Backend()
        elif isinstance(backend, str):
            self.backend = HDFBackend(backend)
        else:
            self.backend = backend

        self.info = info

        self.all_moves = (
            self.moves if not self.has_reversible_jump else self.moves + self.rj_moves
        )
        # Deal with re-used backends
        if not self.backend.initialized:
            self._previous_state = None
            self.reset(
                branch_names=branch_names,
                ntemps=self.ntemps,
                nleaves_max=nleaves_max,
                rj=self.has_reversible_jump,
                moves=self.all_moves,
                **info
            )
            state = np.random.get_state()
        else:
            # Check the backend shape
            for i, (name, shape) in enumerate(self.backend.shape.items()):
                test_shape = (
                    self.ntemps,
                    self.nwalkers,
                    self.nleaves_max[i],
                    self.ndims[i],
                )
                if shape != test_shape:
                    raise ValueError(
                        (
                            "the shape of the backend ({0}) is incompatible with the "
                            "shape of the sampler ({1} for model {2})"
                        ).format(shape, test_shape, name)
                    )

            # Get the last random state
            state = self.backend.random_state
            if state is None:
                state = np.random.get_state()

            # Grab the last step so that we can restart
            it = self.backend.iteration
            if it > 0:
                self._previous_state = self.get_last_sample()

        # This is a random number generator that we can easily set the state
        # of without affecting the numpy-wide generator
        self._random = np.random.mtrand.RandomState()
        self._random.set_state(state)

        # Do a little bit of _magic_ to make the likelihood call with
        # ``args`` and ``kwargs`` pickleable.
        self.log_like_fn = _FunctionWrapper(log_like_fn, args, kwargs)

        self.all_walkers = self.nwalkers * self.ntemps
        self.verbose = verbose

        # prepare plotting
        # TODO: adjust plotting maybe?
        self.plot_iterations = plot_iterations

        if plot_generator is None and self.plot_iterations > 0:
            # set to default if not provided
            if plot_name is not None:
                name = plot_name
            else:
                name = "output"
            self.plot_generator = PlotContainer(
                fp=name, backend=self.backend, thin_chain_by_ac=True
            )
        elif self.plot_iterations > 0:
            self.plot_generator = plot_generator

        # prepare stopping functions
        self.stopping_fn = stopping_fn
        self.stopping_iterations = stopping_iterations

        # prepare update functions
        self.update_fn = update_fn
        self.update_iterations = update_iterations

    @property
    def random_state(self):
        """
        The state of the internal random number generator. In practice, it's
        the result of calling ``get_state()`` on a
        ``numpy.random.mtrand.RandomState`` object. You can try to set this
        property but be warned that if you do this and it fails, it will do
        so silently.

        """
        return self._random.get_state()

    @random_state.setter  # NOQA
    def random_state(self, state):
        """
        Try to set the state of the random number generator but fail silently
        if it doesn't work. Don't say I didn't warn you...

        """
        try:
            self._random.set_state(state)
        except:
            pass

    @property
    def priors(self):
        """
        Return the priors in the sampler.

        """
        return self._priors

    @priors.setter
    def priors(self, priors):
        """Set priors information.
        
        This performs checks to make sure the inputs are okay.

        """
        if isinstance(priors, dict):
            # TODO: do checks on all priors, not just first
            test = priors[list(priors.keys())[0]]
            if isinstance(test, dict):
                # check all dists
                for name, priors_temp in priors.items():
                    for ind, dist in priors_temp.items():
                        if not hasattr(dist, "logpdf"):
                            raise ValueError(
                                "Distribution for model {0} and index {1} does not have logpdf method.".format(
                                    name, ind
                                )
                            )
                self._priors = {
                    name: ProbDistContainer(priors_temp)
                    for name, priors_temp in priors.items()
                }

            elif isinstance(test, ProbDistContainer):
                self._priors = priors

            elif hasattr(test, "logpdf"):
                self._priors = {"model_0": ProbDistContainer(priors)}

            else:
                raise ValueError(
                    "priors dictionary items must be dictionaries with prior information or instances of the ProbDistContainer class."
                )

        elif isinstance(priors, ProbDistContainer):
            self._priors = {"model_0": priors}

        else:
            raise ValueError("Priors must be a dictionary.")

        return

    @property
    def iteration(self):
        return self.backend.iteration

    def reset(self, **info):
        """
        Reset the backend.

        Args:
            **info (dict, optional): information to pass to backend reset method.

        """
        self.backend.reset(self.nwalkers, self.ndims, **info)

    def __getstate__(self):
        # In order to be generally picklable, we need to discard the pool
        # object before trying.
        d = self.__dict__
        d["pool"] = None
        return d

    def get_model(self):
        """Get ``Model`` object from sampler

        The model object is used to pass necessary information to the
        proposals. This method can be used to retrieve the ``model`` used
        in the sampler from outside the sampler.

        Returns:
            :class:`Model`: ``Model`` object used by sampler.

        """
        # Set up a wrapper around the relevant model functions
        if self.pool is not None:
            map_fn = self.pool.map
        else:
            map_fn = map

        # setup model framework for passing necessary items
        model = Model(
            self.log_like_fn,
            self.compute_log_like,
            self.compute_log_prior,
            self.temperature_control,
            map_fn,
            self._random,
        )
        return model

    def sample(
        self,
        initial_state,
        iterations=1,
        tune=False,
        skip_initial_state_check=True,
        thin_by=1,
        store=True,
        progress=False,
    ):
        """Advance the chain as a generator

        Args:
            initial_state (State or ndarray[ntemps, nwalkers, nleaves_max, ndim] or dict): The initial
                :class:`State` or positions of the walkers in the
                parameter space. If multiple branches used, must be dict with keys
                as the ``branch_names`` and values as the positions.
            iterations (int or None, optional): The number of steps to generate.
                ``None`` generates an infinite stream (requires ``store=False``).
                (default: 1)
            tune (bool, optional): If ``True``, the parameters of some moves
                will be automatically tuned. (default: ``False``)
            thin_by (int, optional): If you only want to store and yield every
                ``thin_by`` samples in the chain, set ``thin_by`` to an
                integer greater than 1. When this is set, ``iterations *
                thin_by`` proposals will be made. (default: 1)
            store (bool, optional): By default, the sampler stores in the backend
                the positions (and other information) of the samples in the
                chain. If you are using another method to store the samples to
                a file or if you don't need to analyze the samples after the
                fact (for burn-in for example) set ``store`` to ``False``. (default: ``True``)
            progress (bool or str, optional): If ``True``, a progress bar will
                be shown as the sampler progresses. If a string, will select a
                specific ``tqdm`` progress bar - most notable is
                ``'notebook'``, which shows a progress bar suitable for
                Jupyter notebooks.  If ``False``, no progress bar will be
                shown. (default: ``False``)
            skip_initial_state_check (bool, optional): If ``True``, a check
                that the initial_state can fully explore the space will be
                skipped. If using reversible jump, the user needs to ensure this on their own 
                (``skip_initial_state_check``is set to ``False`` in this case.
                (default: ``True``)

        Returns:
            State: Every ``thin_by`` steps, this generator yields the :class:`State` of the ensemble.

        Raises:
            ValueError: Improper initialization.

        """
        if iterations is None and store:
            raise ValueError("'store' must be False when 'iterations' is None")

        # Interpret the input as a walker state and check the dimensions.
        state = State(initial_state, copy=True)

        # Check the backend shape
        for i, (name, branch) in enumerate(state.branches.items()):
            ntemps_, nwalkers_, nleaves_, ndim_ = branch.shape
            if (ntemps_, nwalkers_, nleaves_, ndim_) != (
                self.ntemps,
                self.nwalkers,
                self.nleaves_max[i],
                self.ndims[i],
            ):
                raise ValueError("incompatible input dimensions")

        # do an initial state check if is requested and we are not using reversible jump
        if (not skip_initial_state_check) and (
            not walkers_independent(state.coords) and not self.has_reversible_jump
        ):
            raise ValueError(
                "Initial state has a large condition number. "
                "Make sure that your walkers are linearly independent for the "
                "best performance"
            )

        # get log prior and likelihood if not provided in the initial state
        if state.log_prior is None:
            coords = state.branches_coords
            inds = state.branches_inds
            state.log_prior = self.compute_log_prior(coords, inds=inds)

        if state.log_like is None:
            coords = state.branches_coords
            inds = state.branches_inds
            state.log_like, state.blobs = self.compute_log_like(
                coords,
                inds=inds,
                logp=state.log_prior,
                supps=state.supplimental,  # only used if self.provide_supplimental is True
                branch_supps=state.branches_supplimental,  # only used if self.provide_supplimental is True
            )

        if np.shape(state.log_like) != (self.ntemps, self.nwalkers):
            raise ValueError("incompatible input dimensions")
        if np.shape(state.log_prior) != (self.ntemps, self.nwalkers):
            raise ValueError("incompatible input dimensions")

        # Check to make sure that the probability function didn't return
        # ``np.nan``.
        if np.any(np.isnan(state.log_like)):
            raise ValueError("The initial log_like was NaN")

        if np.any(np.isinf(state.log_like)):
            raise ValueError("The initial log_like was +/- infinite")

        if np.any(np.isnan(state.log_prior)):
            raise ValueError("The initial log_prior was NaN")

        if np.any(np.isinf(state.log_prior)):
            raise ValueError("The initial log_prior was +/- infinite")

        # Check that the thin keyword is reasonable.
        thin_by = int(thin_by)
        if thin_by <= 0:
            raise ValueError("Invalid thinning argument")

        yield_step = thin_by
        checkpoint_step = thin_by
        if store:
            self.backend.grow(iterations, state.blobs)

        # get the model object
        model = self.get_model()

        # Inject the progress bar
        total = None if iterations is None else iterations * yield_step
        with get_progress_bar(progress, total) as pbar:
            i = 0
            for _ in count() if iterations is None else range(iterations):
                for _ in range(yield_step):
                    # in model moves
                    accepted = np.zeros((self.ntemps, self.nwalkers))
                    for repeat in range(self.num_repeats_in_model):

                        # Choose a random move
                        move = self._random.choice(self.moves, p=self.weights)

                        # Propose (in model)
                        state, accepted_out = move.propose(model, state)
                        accepted += accepted_out
                        if self.ntemps > 1:
                            in_model_swaps = move.temperature_control.swaps_accepted
                        else:
                            in_model_swaps = None

                        state.random_state = self.random_state

                        if tune:
                            move.tune(state, accepted_out)

                    if self.has_reversible_jump:
                        rj_accepted = np.zeros((self.ntemps, self.nwalkers))
                        for repeat in range(self.num_repeats_rj):
                            rj_move = self._random.choice(
                                self.rj_moves, p=self.rj_weights
                            )

                            # Propose (Between models)
                            state, rj_accepted_out = rj_move.propose(model, state)
                            rj_accepted += rj_accepted_out
                            # Again commenting out this section: We do not control temperature on RJ moves
                            # if self.ntemps > 1:
                            #     rj_swaps = rj_move.temperature_control.swaps_accepted
                            # else:
                            #     rj_swaps = None
                            rj_swaps = None

                            state.random_state = self.random_state

                            if tune:
                                rj_move.tune(state, rj_accepted_out)

                    else:
                        rj_accepted = None
                        rj_swaps = None

                    # Save the new step
                    if store and (i + 1) % checkpoint_step == 0:

                        moves_accepted_fraction = [
                            move_tmp.acceptance_fraction for move_tmp in self.all_moves
                        ]
                        self.backend.save_step(
                            state,
                            accepted,
                            rj_accepted=rj_accepted,
                            swaps_accepted=in_model_swaps,
                            moves_accepted_fraction=moves_accepted_fraction,
                        )

                    pbar.update(1)
                    i += 1

                # Yield the result as an iterator so that the user can do all
                # sorts of fun stuff with the results so far.
                yield state

    def run_mcmc(
        self, initial_state, nsteps, burn=None, post_burn_update=False, **kwargs
    ):
        """
        Iterate :func:`sample` for ``nsteps`` iterations and return the result.

        Args:
            initial_state (State or ndarray[ntemps, nwalkers, nleaves_max, ndim] or dict): The initial
                :class:`State` or positions of the walkers in the
                parameter space. If multiple branches used, must be dict with keys
                as the ``branch_names`` and values as the positions.
            nsteps (int): The number of steps to generate. The total number of proposals is ``nsteps * thin_by``.
            burn (int, optional): Number of burn steps to run before storing information. The ``thin_by`` kwarg is ignored when counting burn steps since there is no storage (equivalent to ``thin_by=1``).
            post_burn_update (bool, optional): If ``True``, run ``update_fn`` after burn in. 

        Other parameters are directly passed to :func:`sample`.

        Returns:
            State: This method returns the most recent result from :func:`sample`.

        Raises:
            ValueError: ``If initial_state`` is None and ``run_mcmc`` has never been called.

        """
        if initial_state is None:
            if self._previous_state is None:
                raise ValueError(
                    "Cannot have `initial_state=None` if run_mcmc has never "
                    "been called."
                )
            initial_state = self._previous_state

        # setup thin_by info
        thin_by = 1 if "thin_by" not in kwargs else kwargs["thin_by"]

        # run burn in
        if burn is not None and burn != 0:
            if self.verbose:
                print("Start burn")

            # prepare kwargs that relate to burn
            burn_kwargs = deepcopy(kwargs)
            burn_kwargs["store"] = False
            burn_kwargs["thin_by"] = 1
            i = 0
            for results in self.sample(initial_state, iterations=burn, **burn_kwargs):
                # if updating and using burn_in, need to make sure it does not use
                # previous chain samples since they are not stored.
                if (
                    self.update_iterations > 0
                    and self.update_fn is not None
                    and (i + 1) % (self.update_iterations * thin_by) == 0
                ):
                    self.update_fn(i, results, self)
                i += 1

            # run post-burn update
            if post_burn_update and self.update_fn is not None:
                self.update_fn(i, results, self)

            initial_state = results
            if self.verbose:
                print("Finish burn")

        if nsteps == 0:
            return initial_state

        results = None

        i = 0
        for results in self.sample(initial_state, iterations=nsteps, **kwargs):

            # diagnostic plots
            # TODO: adjust diagnostic plots
            if self.plot_iterations > 0 and (i + 1) % (self.plot_iterations) == 0:
                self.plot_generator.generate_plot_info()  # TODO: remove defaults

            # check for stopping before updating
            if (
                self.stopping_iterations > 0
                and self.stopping_fn is not None
                and (i + 1) % (self.stopping_iterations) == 0
            ):
                stop = self.stopping_fn(i, results, self)

                if stop:
                    break

            # update after diagnostic and stopping check
            if (
                self.update_iterations > 0
                and self.update_fn is not None
                and (i + 1) % (self.update_iterations) == 0
            ):
                self.update_fn(i, results, self)

            i += 1

        # Store so that the ``initial_state=None`` case will work
        self._previous_state = results

        return results

    def compute_log_prior(self, coords, inds=None):
        """Calculate the vector of log-prior for the walkers

        Args:
            coords (dict): Keys are ``branch_names`` and values are
                the position np.arrays[ntemps, nwalkers, nleaves_max, ndim].
                This dictionary is created with the ``branches_coords`` attribute
                from :class:`State`.
            inds (dict, optional): Keys are ``branch_names`` and values are
                the ``inds`` np.arrays[ntemps, nwalkers, nleaves_max] that indicates
                which leaves are being used. This dictionary is created with the
                ``branches_inds`` attribute from :class:`State`.
                (default: ``None``)

        Returns:
            np.ndarray[ntemps, nwalkers]: Prior Values

        """

        # get number of temperature and walkers
        ntemps, nwalkers, _, _ = coords[list(coords.keys())[0]].shape

        if inds is None:
            # default use all sources
            inds = {
                name: np.full(coords[name].shape[:-1], True, dtype=bool)
                for name in coords
            }

        # take information out of dict and spread to x1..xn
        x_in = {}
        if self.provide_groups:

            # get group information from the inds dict
            groups = groups_from_inds(inds)

            # get the coordinates that are used
            for i, (name, coords_i) in enumerate(coords.items()):
                x_in[name] = coords_i[inds[name]]

            prior_out = np.zeros((ntemps * nwalkers))
            for name in x_in:
                # get prior for individual binaries
                prior_out_temp = self.priors[name].logpdf(x_in[name])

                # arrange prior values by groups
                # TODO: vectorize this?
                for i in np.unique(groups[name]):
                    # which members are in the group i
                    inds_temp = np.where(groups[name] == i)[0]
                    # num_in_group = len(inds_temp)

                    # add to the prior for this group
                    prior_out[i] += prior_out_temp[inds_temp].sum()

            # reshape
            prior_out = prior_out.reshape(ntemps, nwalkers)

        else:
            # flatten coordinate arrays
            for i, (name, coords_i) in enumerate(coords.items()):
                ntemps, nwalkers, nleaves_max, ndim = coords_i.shape

                x_in[name] = coords_i.reshape(-1, ndim)

            prior_out = np.zeros((ntemps, nwalkers))
            for name in x_in:
                ntemps, nwalkers, nleaves_max, ndim = coords[name].shape

                prior_out_temp = (
                    self.priors[name]
                    .logpdf(x_in[name])
                    .reshape(ntemps, nwalkers, nleaves_max)
                )

                # fix any infs / nans from binaries that are not being used (inds == False)
                prior_out_temp[~inds[name]] = 0.0

                # vectorized because everything is rectangular (no groups to indicate model difference)
                prior_out += prior_out_temp.sum(axis=-1)

        return prior_out

    def compute_log_like(
        self, coords, inds=None, logp=None, supps=None, branch_supps=None
    ):
        """Calculate the vector of log-likelihood for the walkers

        Args:
            coords (dict): Keys are ``branch_names`` and values are
                the position np.arrays[ntemps, nwalkers, nleaves_max, ndim].
                This dictionary is created with the ``branches_coords`` attribute
                from :class:`State`.
            inds (dict, optional): Keys are ``branch_names`` and values are
                the inds np.arrays[ntemps, nwalkers, nleaves_max] that indicates
                which leaves are being used. This dictionary is created with the
                ``branches_inds`` attribute from :class:`State`.
                (default: ``None``)
            logp (np.ndarray[ntemps, nwalkers], optional): Log prior values associated
                with all walkers. If not provided, it will be calculated because
                if a walker has logp = -inf, its likelihood is not calculated.
                This prevents evaluting likelihood outside the prior.
                (default: ``None``)

        Returns:
            tuple: Carries log-likelihood and blob information.
                First entry is np.ndarray[ntemps, nwalkers] with values corresponding
                to the log likelihood of each walker. Second entry is ``blobs``.

         Raises:
            ValueError: Infinite or NaN values in parameters.

        """

        # Check that the parameters are in physical ranges.
        for ptemp in coords.values():
            if np.any(np.isinf(ptemp)):
                raise ValueError("At least one parameter value was infinite")
            if np.any(np.isnan(ptemp)):
                raise ValueError("At least one parameter value was NaN")

        # if inds not provided, use all
        if inds is None:
            inds = {
                name: np.full(coords[name].shape[:-1], True, dtype=bool)
                for name in coords
            }

        # if no prior values are added, compute_prior
        # this is necessary to ensure Likelihood is not evaluated outside of the prior
        if logp is None:
            logp = self.compute_log_prior(coords, inds=inds)

        # if all points are outside the prior
        if np.all(np.isinf(logp)):
            warnings.warn(
                "All points input for the Likelihood have a log prior of -inf."
            )
            return np.full_like(logp, -1e300), None

        # do not run log likelihood where logp = -inf
        inds_copy = deepcopy(inds)
        inds_bad = np.where(np.isinf(logp))
        for key in inds_copy:
            inds_copy[key][inds_bad] = False

            # if inds_keep in branch supps, indicate which to not keep
            if (
                branch_supps is not None
                and branch_supps[key] is not None
                and "inds_keep" in branch_supps[key]
            ):
                # TODO: indicate specialty of inds_keep in branch_supp
                branch_supps[key][inds_bad] = {"inds_keep": False}

        # take information out of dict and spread to x1..xn
        x_in = {}
        if self.provide_supplimental:
            if supps is None and branch_supps is None:
                raise ValueError(
                    """supps and branch_supps are both None. If self.provide_supplimental
                       is True, must provide some supplimental information."""
                )
            if branch_supps is not None:
                branch_supps_in = {}

        # determine groupings from inds
        groups = groups_from_inds(inds_copy)

        # need to map group inds properly
        # this is the unique group indexes
        unique_groups = np.unique(
            np.concatenate([groups_i for groups_i in groups.values()])
        )

        # this is the map to those indexes that are used in the likelihood
        groups_map = np.arange(len(unique_groups))

        # get the indices with groups_map for the Likelihood
        ll_groups = {}
        for key, group in groups.items():
            # get unique groups in this sub-group (or branch)
            temp_unique_groups, inverse = np.unique(group, return_inverse=True)

            # use groups_map by finding where temp_unique_groups overlaps with unique_groups
            keep_groups = groups_map[np.in1d(unique_groups, temp_unique_groups)]

            # fill group information for Likelihood
            ll_groups[key] = keep_groups[inverse]

        for i, (name, coords_i) in enumerate(coords.items()):
            ntemps, nwalkers, nleaves_max, ndim = coords_i.shape
            nwalkers_all = ntemps * nwalkers

            # fill x_values properly into dictionary
            x_in[name] = coords_i[inds_copy[name]]

            # prepare branch supplimentals for each branch
            if self.provide_supplimental:
                if branch_supps is not None:  #  and
                    if branch_supps[name] is not None:
                        # index the branch supps
                        # it will carry in a dictionary of information
                        branch_supps_in[name] = branch_supps[name][inds_copy[name]]
                    else:
                        # fill with None if this branch does not have a supplimental
                        branch_supps_in[name] = None

        # deal with overall supplimental not specific to the branches
        if self.provide_supplimental:
            if supps is not None:
                # get the flattened supplimental
                # this will produce the shape (ntemps * nwalkers,...)
                temp = supps.flat

                # unique_groups will properly index the flattened array
                supps_in = {
                    name: values[unique_groups] for name, values in temp.items()
                }

        # prepare group information
        # this gets the group_map indexing into a list
        groups_in = list(ll_groups.values())

        # if only one branch, take the group array out of the list
        if len(groups_in) == 1:
            groups_in = groups_in[0]

        # list of paramter arrays
        params_in = list(x_in.values())

        # Likelihoods are vectorized across groups
        if self.vectorize:

            # prepare args list
            args_in = []

            # when vectorizing, if params_in has one entry, take out of list
            if len(params_in) == 1:
                params_in = params_in[0]

            # add parameters to args
            args_in.append(params_in)

            # if providing groups, add to args
            if self.provide_groups:
                args_in.append(groups_in)

            # prepare supplimentals as kwargs to the Likelihood
            kwargs_in = {}
            if self.provide_supplimental:
                if supps is not None:
                    kwargs_in["supps"] = supps_in
                if branch_supps is not None:
                    # get list of branch_supps values
                    branch_supps_in_2 = list(branch_supps_in.values())

                    # if only one entry, take out of list
                    if len(branch_supps_in_2) == 1:
                        kwargs_in["branch_supps"] = branch_supps_in_2[0]

                    else:
                        kwargs_in["branch_supps"] = branch_supps_in_2

            # provide args, kwargs as a tuple
            args_and_kwargs = (args_in, kwargs_in)

            # get vectorized results
            results = self.log_like_fn(args_and_kwargs)

        # each Likelihood is computed individually
        else:

            # if groups in is an array, need to put it in a list.
            if isinstance(groups_in, np.ndarray):
                groups_in = [groups_in]

            # prepare input args for all Likelihood calls
            # to be spread out with map functions below
            args_in = []

            # each individual group in the groups_map
            for group_i in groups_map:

                # args and kwargs for the individual Likelihood
                arg_i = [None for _ in self.branch_names]
                kwarg_i = {}

                # iterate over the group information from the branches
                for branch_i, groups_in_set in enumerate(groups_in):
                    # which entries in this branch are in the overall group tested
                    # this accounts for multiple leaves (or model counts)
                    inds_keep = np.where(groups_in_set == group_i)[0]

                    branch_name_i = self.branch_names[branch_i]

                    if inds_keep.shape[0] > 0:
                        # get parameters
                        params = params_in[branch_i][inds_keep]

                        # add them to the specific args for this Likelihood
                        arg_i[branch_i] = params
                        if self.provide_supplimental:
                            if supps is not None:
                                # supps are specific to each group
                                kwarg_i["supps"] = supps_in[group_i]
                            if branch_supps is not None:
                                # make sure there is a dictionary ready in this kwarg dictionary
                                if "branch_supps" not in kwarg_i:
                                    kwarg_i["branch_supps"] = {}

                                # fill these branch supplimentals for the specific group
                                if branch_supps_in[branch_name_i] is not None:
                                    # get list of branch_supps values
                                    kwarg_i["branch_supps"][
                                        branch_name_i
                                    ] = branch_supps_in[branch_name_i][inds_keep]
                                else:
                                    kwarg_i["branch_supps"][branch_name_i] = None

                # if only one model type, will take out of groups
                add_term = arg_i[0] if len(groups_in) == 1 else arg_i

                # based on how this is dealth with in the _FunctionWrapper
                # add_term is wrapped in a list
                args_in.append([[add_term], kwarg_i])

            # If the `pool` property of the sampler has been set (i.e. we want
            # to use `multiprocessing`), use the `pool`'s map method.
            # Otherwise, just use the built-in `map` function.
            if self.pool is not None:
                map_func = self.pool.map

            else:
                map_func = map

            # get results and turn into an array
            results = np.asarray(list(map_func(self.log_like_fn, args_in)))

        assert isinstance(results, np.ndarray)

        # -1e300 because -np.inf screws up state acceptance transfer in proposals
        ll = np.full(nwalkers_all, -1e300)
        inds_fix_zeros = np.delete(np.arange(nwalkers_all), unique_groups)

        # make sure second dimension is not 1
        if results.ndim == 2 and results.shape[1] == 1:
            results = np.squeeze(results)

        # parse the results if it has blobs
        if results.ndim == 2:
            # get the results and put into groups that were analyzed
            ll[unique_groups] = results[:, 0]

            # fix groups that were not analyzed
            ll[inds_fix_zeros] = self.fill_zero_leaves_val

            # deal with blobs
            blobs_out = np.zeros((nwalkers_all, results.shape[1] - 1))
            blobs_out[unique_groups] = results[:, 1:]

        elif results.dtype == "object":
            # TODO: check blobs and add this capability
            raise NotImplementedError

        else:
            # no blobs
            ll[unique_groups] = results
            ll[inds_fix_zeros] = self.fill_zero_leaves_val

            blobs_out = None

        if False:  # self.provide_supplimental:
            # TODO: need to think about how to return information, we may need to add a function to do that
            if branch_supps is not None:
                for name_i, name in enumerate(branch_supps):
                    if branch_supps[name] is not None:
                        # TODO: better way to do this? limit to
                        if "inds_keep" in branch_supps[name]:
                            inds_back = branch_supps[name][:]["inds_keep"]
                            inds_back2 = branch_supps_in[name]["inds_keep"]
                        else:
                            inds_back = inds_copy[name]
                            inds_back2 = slice(None)
                        try:
                            branch_supps[name][inds_back] = {
                                key: branch_supps_in_2[name_i][key][inds_back2]
                                for key in branch_supps_in_2[name_i]
                            }
                        except ValueError:
                            breakpoint()
                            branch_supps[name][inds_back] = {
                                key: branch_supps_in_2[name_i][key][inds_back2]
                                for key in branch_supps_in_2[name_i]
                            }

        # return Likelihood and blobs
        return ll.reshape(ntemps, nwalkers), blobs_out

    @property
    def acceptance_fraction(self):
        """The fraction of proposed steps that were accepted"""
        return self.backend.accepted / float(self.backend.iteration)

    @property
    def rj_acceptance_fraction(self):
        """The fraction of proposed reversible jump steps that were accepted"""
        if self.has_reversible_jump:
            return self.backend.rj_accepted / float(self.backend.iteration)
        else:
            return None

    @property
    def swap_acceptance_fraction(self):
        """The fraction of proposed steps that were accepted"""
        # print(self.backend.iteration) # np.sum(self.backend.accepted)
        # breakpoint()
        return self.backend.swaps_accepted / float(self.backend.iteration)

    @property
    def rj_swap_acceptance_fraction(self):
        """The fraction of proposed reversible jump steps that were accepted"""
        if self.has_reversible_jump:
            # print(self.backend.iteration, np.sum(self.backend.rj_accepted))
            return self.backend.rj_swaps_accepted / float(self.backend.iteration)
        else:
            return None

    def get_chain(self, **kwargs):
        return self.get_value("chain", **kwargs)

    get_chain.__doc__ = Backend.get_chain.__doc__

    def get_blobs(self, **kwargs):
        return self.get_value("blobs", **kwargs)

    get_blobs.__doc__ = Backend.get_blobs.__doc__

    def get_log_like(self, **kwargs):
        return self.get_value("log_like", **kwargs)

    get_log_like.__doc__ = Backend.get_log_like.__doc__

    def get_log_prior(self, **kwargs):
        return self.get_value("log_like", **kwargs)

    get_log_prior.__doc__ = Backend.get_log_prior.__doc__

    def get_inds(self, **kwargs):
        return self.get_value("inds", **kwargs)

    get_inds.__doc__ = Backend.get_inds.__doc__

    def get_nleaves(self, **kwargs):
        return self.backend.get_nleaves(**kwargs)

    get_nleaves.__doc__ = Backend.get_nleaves.__doc__

    def get_last_sample(self, **kwargs):
        return self.backend.get_last_sample()

    get_last_sample.__doc__ = Backend.get_last_sample.__doc__

    def get_betas(self, **kwargs):
        return self.backend.get_betas(**kwargs)

    get_betas.__doc__ = Backend.get_betas.__doc__

    def get_value(self, name, **kwargs):
        """Get a specific value"""
        return self.backend.get_value(name, **kwargs)

    def get_autocorr_time(self, **kwargs):
        """Compute autocorrelation time through backend."""
        return self.backend.get_autocorr_time(**kwargs)

    get_autocorr_time.__doc__ = Backend.get_autocorr_time.__doc__


class _FunctionWrapper(object):
    """
    This is a hack to make the likelihood function pickleable when ``args``
    or ``kwargs`` are also included.

    """

    def __init__(
        self, f, args, kwargs,
    ):
        self.f = f
        self.args = [] if args is None else args
        self.kwargs = {} if kwargs is None else kwargs

    def __call__(self, args_and_kwargs):
        """
        Internal function that takes a tuple (args, kwargs) for entrance into the Likelihood.

        ``self.args`` and ``self.kwargs`` are added to these inputs.
        
        """

        args_in_add, kwargs_in_add = args_and_kwargs

        try:
            args_in = args_in_add + type(args_in_add)(self.args)
            kwargs_in = {**kwargs_in_add, **self.kwargs}
            # TODO: this may have pickle issue with multiprocessing (kwargs_in)

            out = self.f(*args_in, **kwargs_in)
            return out

        except:  # pragma: no cover
            import traceback

            print("eryn: Exception while calling your likelihood function:")
            print("  args added:", args_in_add)
            print("  args:", self.args)
            print("  kwargs added:", kwargs_in_add)
            print("  kwargs:", self.kwargs)
            print("  exception:")
            traceback.print_exc()
            raise


def walkers_independent(coords_in):
    """Determine if walkers are independent

    Orginall from ``emcee``.
    
    Args:
        coords_in (np.ndarray[ntemps, nwalkers, nleaves_max, ndim]): Coordinates of the walkers.

    Returns:
        bool: If walkers are independent.
    
    """
    # make sure it is 4-dimensional and reshape
    # so it groups by temperature and walker
    assert coords_in.ndim == 4
    ntemps, nwalkers, nleaves_max, ndim = coords_in.shape
    coords = coords_in.reshape(ntemps * nwalkers, nleaves_max * ndim)

    # make sure all coordinates are finite
    if not np.all(np.isfinite(coords)):
        return False

    # roughly determine covariance information
    C = coords - np.mean(coords, axis=0)[None, :]
    C_colmax = np.amax(np.abs(C), axis=0)
    if np.any(C_colmax == 0):
        return False
    C /= C_colmax
    C_colsum = np.sqrt(np.sum(C ** 2, axis=0))
    C /= C_colsum
    return np.linalg.cond(C.astype(float)) <= 1e8
