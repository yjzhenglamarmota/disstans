"""
This module contains the global default configuration dictionary.
"""

# set global defaults that can be overriden by the user
defaults = {}
"""
Miscellaneous, global default configurations.

For the default values, refer to the signature above. The keywords are organized
and explained as follows:

+-------------+------------------------+---------------------------------------------------+
| Group       | Keyword                | Description                                       |
+=============+========================+===================================================+
| ``general`` | ``num_threads``        | Number of threads to use, initialized at          |
|             |                        | :mod:`~geonat.tools` import. Defaults two half    |
|             |                        | of the available ones, ``0`` means no             |
|             |                        | parallelization.                                  |
+-------------+------------------------+---------------------------------------------------+
| ``gui``     | ``projection``         | Map projection.                                   |
+-------------+------------------------+---------------------------------------------------+
|             | ``coastlines_show``    | If ``True``, show coastlines on map.              |
+-------------+------------------------+---------------------------------------------------+
|             | ``coastlines_res``     | Resolution of coastlines, possible values are     |
|             |                        | ``10m``, ``50m`` and ``110m``.                    |
+-------------+------------------------+---------------------------------------------------+
|             | ``wmts_show``          | If ``True``, show background WMTS imagery.        |
+-------------+------------------------+---------------------------------------------------+
|             | ``wmts_server``        | URL of the WMTS server.                           |
+-------------+------------------------+---------------------------------------------------+
|             | ``wmts_layer``         | Layer name to use of the WMTS server.             |
+-------------+------------------------+---------------------------------------------------+
|             | ``wmts_alpha``         | Transparency of the background imagery (0-1).     |
+-------------+------------------------+---------------------------------------------------+
|             | ``plot_sigmas``        | If greater than 0, plot the specified amount of   |
|             |                        | standard deviations as shading when uncertainties |
|             |                        | are present in the timeseries.                    |
+-------------+------------------------+---------------------------------------------------+
|             | ``plot_sigmas_alphas`` | Transparency of the uncertainty shading (0-1).    |
+-------------+------------------------+---------------------------------------------------+
| ``clean``   | ``min_obs``            | Drop timeseries with less that ``min_obs``        |
|             |                        | observations.                                     |
+-------------+------------------------+---------------------------------------------------+
|             | ``std_outlier``        | Outliers are defined by being at least            |
|             |                        | ``std_outliers`` standard deviations away from    |
|             |                        | the reference.                                    |
+-------------+------------------------+---------------------------------------------------+
|             | ``min_clean_obs``      | After cleaning, drop timeseries with less than    |
|             |                        | ``min_clean_obs`` observations.                   |
+-------------+------------------------+---------------------------------------------------+
|             | ``std_thresh``         | After cleaning, drop timeseries if its standard   |
|             |                        | deviations is higher than ``std_thresh``          |
|             |                        | (data units).                                     |
+-------------+------------------------+---------------------------------------------------+
| ``prior``   | ``mu``                 | Shear modulus μ [GPa] of the elastic half space.  |
+-------------+------------------------+---------------------------------------------------+
|             | ``alpha``              | Medium constant α=(λ+μ)/(λ+2μ) [-], where λ is    |
|             |                        | the first Lamé parameter and μ the second one     |
|             |                        | (shear modulus). It is related to Poisson's ratio |
|             |                        | ν by α=1/(2-2ν).                                  |
+-------------+------------------------+---------------------------------------------------+
|             | ``threshold``          | Minimum amount of calculated displacement [mm]    |
|             |                        | that a station needs to surpass in order for a    |
|             |                        | step to be added to the model.                    |
+-------------+------------------------+---------------------------------------------------+
| ``solvers`` | ``reweight_func``      | Specifies the reweighting function.               |
+-------------+------------------------+---------------------------------------------------+
|             | ``reweight_eps``       | A small number used to stabilize the reweighting. |
+-------------+------------------------+---------------------------------------------------+
|             | ``reweight_usescales`` | If ``True``, the reweighting function checks for  |
|             |                        | model-internal scales like the ones used by       |
|             |                        | :class:`~geonat.models.SplineSet` and takes them  |
|             |                        | into account when calculating the new penalties.  |
+-------------+------------------------+---------------------------------------------------+

For more about the WMTS background imagery, see `Cartopy's documentation <WMTS_>`_.

.. _`WMTS`: https://scitools.org.uk/cartopy/docs/latest/matplotlib/\
geoaxes.html#cartopy.mpl.geoaxes.GeoAxes.add_wmts
"""

# general
defaults["general"] = {"num_threads": None}
# GUI
defaults["gui"] = {"projection": "Mercator",
                   "coastlines_show": True,
                   "coastlines_res": "50m",
                   "wmts_show": False,
                   "wmts_server": "https://server.arcgisonline.com/arcgis/rest/services/"
                                  "World_Imagery/MapServer/WMTS/1.0.0/WMTSCapabilities.xml",
                   "wmts_layer": "World_Imagery",
                   "wmts_alpha": 0.2,
                   "plot_sigmas": 3,
                   "plot_sigmas_alpha": 0.5}
# cleaning timeseries
defaults["clean"] = {"min_obs": 100,
                     "std_outlier": 5,
                     "min_clean_obs": 100,
                     "std_thresh": 100}
# priors from earthquake catalogs
defaults["prior"] = {"mu": 48,
                     "alpha": 0.667,
                     "threshold": 3}
# for iterative reweighting L1-regularized solver
defaults["solvers"] = {"reweight_func": "log",
                       "reweight_eps": 1e-4,
                       "reweight_usescales": False}
