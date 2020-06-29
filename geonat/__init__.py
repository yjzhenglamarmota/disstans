"""
This is the first module loaded and includes initialization routines
as well as the global default configuration dictionary.
"""

import multiprocessing
from pandas.plotting import register_matplotlib_converters

# package version
__version__ = '0.1.1'

# preparational steps
multiprocessing.set_start_method('spawn', True)
register_matplotlib_converters()

# set global defaults that can be overriden by the user
defaults = {}
"""
Miscellaneous, global default configurations.

For the default values, refer to the signature above. The keywords are organized
and explained as follows:

+-------------+------------------------+----------------------------------------------------------+
| Group       | Keyword                | Description                                              |
+=============+========================+==========================================================+
| ``general`` | ``num_threads``        | Number of threads to use, initialized at                 |
|             |                        | :mod:`~geonat.tools` import. Defaults two half           |
|             |                        | of the available ones, ``0`` means no parallelization.   |
+-------------+------------------------+----------------------------------------------------------+
| ``gui``     | ``projection``         | Map projection.                                          |
+-------------+------------------------+----------------------------------------------------------+
|             | ``coastlines_show``    | If ``True``, show coastlines on map.                     |
+-------------+------------------------+----------------------------------------------------------+
|             | ``coastlines_res``     | Resolution of coastlines, possible values are            |
|             |                        | ``10m``, ``50m`` and ``110m``.                           |
+-------------+------------------------+----------------------------------------------------------+
|             | ``wmts_show``          | If ``True``, show background WMTS imagery.               |
+-------------+------------------------+----------------------------------------------------------+
|             | ``wmts_server``        | URL of the WMTS server.                                  |
+-------------+------------------------+----------------------------------------------------------+
|             | ``wmts_layer``         | Layer name to use of the WMTS server.                    |
+-------------+------------------------+----------------------------------------------------------+
|             | ``wmts_alpha``         | Transparency of the background imagery (0-1).            |
+-------------+------------------------+----------------------------------------------------------+
|             | ``plot_sigmas``        | If greater than 0, plot the specified amount of          |
|             |                        | standard deviations as shading when uncertainties        |
|             |                        | are present in the timeseries.                           |
+-------------+------------------------+----------------------------------------------------------+
|             | ``plot_sigmas_alphas`` | Transparency of the uncertainty shading (0-1).           |
+-------------+------------------------+----------------------------------------------------------+
| ``clean``   | ``min_obs``            | Drop timeseries with less that ``min_obs`` observations. |
+-------------+------------------------+----------------------------------------------------------+
|             | ``std_outlier``        | Outliers are defined by being at least ``std_outliers``  |
|             |                        | standard deviations away from the reference.             |
+-------------+------------------------+----------------------------------------------------------+
|             | ``min_clean_obs``      | After cleaning, drop timeseries with less than           |
|             |                        | ``min_clean_obs`` observations.                          |
+-------------+------------------------+----------------------------------------------------------+
|             | ``std_thresh``         | After cleaning, drop timeseries if its standard          |
|             |                        | deviations is higher than ``std_thresh`` (data units).   |
+-------------+------------------------+----------------------------------------------------------+
| ``prior``   | ``mu``                 | Shear modulus μ [GPa] of the elastic half space.         |
+-------------+------------------------+----------------------------------------------------------+
|             | ``alpha``              | Medium constant α=(λ+μ)/(λ+2μ) [-], where λ is the first |
|             |                        | Lamé parameter and μ the second one (shear modulus).     |
+-------------+------------------------+----------------------------------------------------------+
|             | ``threshold``          | Minimum amount of calculated displacement [mm] that a    |
|             |                        | station needs to surpass in order for a step to be added |
|             |                        | to the model.                                            |
+-------------+------------------------+----------------------------------------------------------+

For more about the WMTS background imagery, see `Cartopy's documentation <WMTS_>`_.

.. _`WMTS`: https://scitools.org.uk/cartopy/docs/latest/matplotlib/geoaxes.html#cartopy.mpl.geoaxes.GeoAxes.add_wmts
"""

# general
defaults["general"] = {"num_threads": None}
# GUI
defaults["gui"] = {"projection": "Mercator",
                   "coastlines_show": True,
                   "coastlines_res": "50m",
                   "wmts_show": False,
                   "wmts_server": "https://server.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/WMTS/1.0.0/WMTSCapabilities.xml",
                   "wmts_layer": "World_Imagery",
                   "wmts_alpha": 0.2,
                   "plot_sigmas": 3,
                   "plot_sigmas_alpha": 0.5}
# cleaning timeseries
defaults["clean"] = {"std_thresh": 100,
                     "std_outlier": 5,
                     "min_obs": 100,
                     "min_clean_obs": 100}
# priors from earthquake catalogs
defaults["prior"] = {"alpha": 10,
                     "mu": 30,
                     "threshold": 3}
