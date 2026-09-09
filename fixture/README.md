# Acceptance fixture

`crs_inspector_fixture.qgz` plus three GeoPackages. Project CRS **EPSG:6318**.

| Layer | CRS | Expected |
|---|---|---|
| `control_projection_only` | EPSG:6318 | same datum as the project: expect NO datum shift. The control. |
| `case_ballpark_nad27` | EPSG:4267 | NAD27 -> NAD83(2011): expect BALLPARK *if* the NADCON5 grids are absent. |
| `case_published_shift` | EPSG:4326 | WGS 84 -> NAD83(2011): expect a shift with a published accuracy. |

## Resource conditions when generated

Classification depends on what PROJ can find, which differs per install.
A clean profile does not mean an identical transformation environment, so
record these again on the machine under test rather than assuming them.

```
QGIS 4.2.2-Belém do Pará
EPSG:4267 -> EPSG:6318: grid us_noaa_nadcon5_nad27_nad83_1986_alaska.tif ABSENT
EPSG:4267 -> EPSG:6318: grid us_noaa_nadcon5_nad27_nad83_1986_conus.tif ABSENT
EPSG:4267 -> EPSG:6318: grid us_noaa_nadcon5_nad83_1986_nad83_1992_alaska.tif ABSENT
EPSG:4267 -> EPSG:6318: grid us_noaa_nadcon5_nad83_1986_nad83_harn_conus.tif ABSENT
EPSG:4267 -> EPSG:6318: grid us_noaa_nadcon5_nad83_1992_nad83_2007_alaska.tif ABSENT
EPSG:4267 -> EPSG:6318: grid us_noaa_nadcon5_nad83_2007_nad83_2011_alaska.tif ABSENT
EPSG:4267 -> EPSG:6318: grid us_noaa_nadcon5_nad83_2007_nad83_2011_conus.tif ABSENT
EPSG:4267 -> EPSG:6318: grid us_noaa_nadcon5_nad83_fbn_nad83_2007_conus.tif ABSENT
EPSG:4267 -> EPSG:6318: grid us_noaa_nadcon5_nad83_harn_nad83_fbn_conus.tif ABSENT
```
