# Eryn: a multi-purpose MCMC sampler

Eryn is an advanced MCMC sampler. It has the capability to run with parallel tempering, multiple model types, and unknown counts within each model type using Reversible Jump MCMC techniques. Eryn is heavily based on [emcee](https://emcee.readthedocs.io/en/stable/). The `emcee` base structure with the Ensemble Sampler, State objects, proposal setup, and storage backends is carried over into Eryn with small changes to account for the increased complexity. In a simple sense, Eryn is an advanced (and slightly more complicated) version of `emcee`. 

If you use Eryn in your publication, please cite the paper [arXiv:2303.02164](https://arxiv.org/abs/2303.02164), its zenodo (# TODO: add zenodo as well), and [emcee](https://emcee.readthedocs.io/en/stable/). The documentation for Eryn can be found here: [mikekatz04.gihub.io/Eryn](https://mikekatz04.gihub.io/Eryn). You will find the code on Github: [github.com/mikekatz04/Eryn](https://github.com/mikekatz04/Eryn). 

## Getting Started

Below is a quick set of instructions to get you started with `eryn`.

```
pip install git+https://github.com/mikekatz04/Eryn.git
```
To import eryn:

```
from eryn.ensemble import EnsembleSampler
```

See [examples notebook](https://github.com/mikekatz04/Eryn/blob/main/examples/Eryn_tutorial.ipynb) for more info.


### Prerequisites

Eryn has only a few python-based dependencies: `tqdm`, `corner` for plotting, `numpy`, `matplotlib`, `cupy` TODO: update this

### Installing

If you are not planning to develop the code, you can just install the latest version with the pip installation technique given above. Otherwise, you can just clone the repo and run `pip install .` inside of the Eryn directory.


## Running the Tests

In the main directory of the package run in the terminal:
```
python -m unittest discover
```


## Contributing

Please read [CONTRIBUTING.md](CONTRIBUTING.md) for details on our code of conduct, and the process for submitting pull requests to us.

## Versioning

We use [SemVer](http://semver.org/) for versioning. For the versions available, see the [tags on this repository](https://github.com/BlackHolePerturbationToolkit/FastEMRIWaveforms/tags).

Current Version: 0.2.2

## Authors

* **Michael Katz**
* Nikos Karnesis
* Natalia Korsokova
* Jonathan Gair

### Contibutors

* Maybe you!

## License

This project is licensed under the GNU License - see the [LICENSE.md](LICENSE.md) file for details.

## Acknowledgments

* TODO: add acknowledgements
