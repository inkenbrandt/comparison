# References

**How these were checked.** On 2026-10-20 every DOI below was resolved through the Crossref
API, and the title, authors, year, journal, volume and pages were compared with the entry.
The role each study plays here follows the project's implementation guide. The full texts
were not re-read for this page, so a statement about a study's method is a pointer to that
study, not a quotation. Supplied PDFs are not redistributed with the package.

## OpenET and eddy covariance evaluations

- **Volk, J. M., Huntington, J. L., Melton, F. S., et al. (2024).** Assessing the accuracy of
  OpenET satellite-based evapotranspiration data to support water resource and land
  management applications. *Nature Water*, 2, 193–205.
  <https://doi.org/10.1038/s44221-023-00181-7>
  *Used for:* paired multi-scale evaluation, energy-balance-corrected references,
  reference-ET-weighted footprints. Its reference-ET weighting differs from weighting by
  measured ET.
- **Knipper, K., Bambach, N., Anderson, M., et al. (2026).** Evaluating OpenET
  evapotranspiration estimates in California vineyards: Insights from the GRAPEX experiment.
  *Agricultural and Forest Meteorology*, 391, 111505.
  <https://doi.org/10.1016/j.agrformet.2026.111505>
  *Used for:* temporal scale, advection and closure sensitivity, management response.
- **Reitz, M., Volk, J. M., Ott, T., et al. (2025).** Performance Mapping and Weighting for
  the Evapotranspiration Models of the OpenET Ensemble. *Water Resources Research*, 61,
  e2024WR038899. <https://doi.org/10.1029/2024WR038899>
  *Used for:* complementary metrics, environmental controls, weighting evaluated at held-back
  sites. The local ensemble here is an extension, not a reproduction.
- **Huntington, J. L., et al. (2022).** Appendix G: Upper Colorado River Basin OpenET
  Intercomparison Summary. Report prepared for the U.S. Bureau of Reclamation. No DOI;
  *not verified through Crossref.*
  *Used for:* reference-ET-weighted footprint sampling, closed and unclosed station ET.
- **Sabiston, L., Sabie, R., Buenemann, M., et al. (2025).** Comparing field-scale eddy
  covariance measurements and crop coefficient estimates of alfalfa evapotranspiration to
  OpenET model estimates and exploring water budget implications in a dryland environment.
  *Irrigation Science*, 43, 655–672 (online 2024).
  <https://doi.org/10.1007/s00271-024-00941-7>
  *Used for:* extraction-area sensitivity. Its unpaired distribution tests are not the main
  test of paired daily accuracy here.
- **Djaman, K., Mohammed, A. T., Koudahe, K. (2023).** Accuracy of Estimated Crop
  Evapotranspiration Using Locally Developed Crop Coefficients against Satellite-Derived
  Crop Evapotranspiration in a Semiarid Climate. *Agronomy*, 13, 1937.
  <https://doi.org/10.3390/agronomy13071937>
  *Used for:* growth-stage and seasonal comparisons. Its reference is crop-coefficient ET,
  not direct EC ET.
- **Tawalbeh, Z. M., Bawazir, A. S., Fernald, A., et al. (2024).** Assessing
  Satellite-Derived OpenET Platform Evapotranspiration of Mature Pecan Orchard in the
  Mesilla Valley, New Mexico. *Remote Sensing*, 16, 1429.
  <https://doi.org/10.3390/rs16081429>
  *Note:* the reference for part of its period is reconstructed from coefficients, not
  contemporaneous EC.
- **Montazar, A., Shields, J. W., Daccache, A., et al. (2026).** Field-scale evaluation of
  OpenET for quantifying consumptive water savings under deficit irrigation in alfalfa.
  *Agricultural Water Management*, 328, 110337.
  <https://doi.org/10.1016/j.agwat.2026.110337>
  *Used for:* event and cumulative-response comparisons. Its irrigated reference uses the
  residual energy balance; deficit comparisons use soil-water depletion.
- **Osorio-Diaz, K., Torres-Rua, A., Yost, M., et al. (2026).** Agricultural water balance
  and variability in Utah irrigation canal companies with OpenET products and spatial data.
  *Irrigation Science*, 44, 128. <https://doi.org/10.1007/s00271-026-01172-8>
  *Used for:* regional water-balance plausibility checks with unresolved exchanges kept in
  the residual.

## Methods

- **Gupta, H. V., Kling, H., Yilmaz, K. K., Martinez, G. F. (2009).** Decomposition of the
  mean squared error and NSE performance criteria: Implications for improving hydrological
  modelling. *Journal of Hydrology*, 377, 80–91.
  <https://doi.org/10.1016/j.jhydrol.2009.08.003> — the KGE implemented (`kge_2009`).
- **Kling, H., Fuchs, M., Paulin, M. (2012).** Runoff conditions in the upper Danube basin
  under an ensemble of climate change scenarios. *Journal of Hydrology*, 424–425, 264–277.
  <https://doi.org/10.1016/j.jhydrol.2012.01.011> — the modified KGE, **not** implemented.
- **Nash, J. E., Sutcliffe, J. V. (1970).** River flow forecasting through conceptual models
  part I — A discussion of principles. *Journal of Hydrology*, 10, 282–290.
  <https://doi.org/10.1016/0022-1694(70)90255-6> — NSE.
- **Künsch, H. R. (1989).** The Jackknife and the Bootstrap for General Stationary
  Observations. *The Annals of Statistics*, 17, 1217–1241.
  <https://doi.org/10.1214/aos/1176347265> — the moving-block bootstrap that step 10 adapts
  (segment-truncated blocks are this package's adaptation).
- **Chu, H., Luo, X., Ouyang, Z., et al. (2021).** Representativeness of Eddy-Covariance
  flux footprints for areas surrounding AmeriFlux sites. *Agricultural and Forest
  Meteorology*, 301–302, 108350. <https://doi.org/10.1016/j.agrformet.2021.108350> — the
  framing of footprint representativeness and the fluxfootprints labels.
- **Kljun, N., Calanca, P., Rotach, M. W., Schmid, H. P. (2015).** A simple two-dimensional
  parameterisation for Flux Footprint Prediction (FFP). *Geoscientific Model Development*,
  8, 3695–3713. <https://doi.org/10.5194/gmd-8-3695-2015> — the footprint model behind the
  user's footprints (computed upstream by fluxfootprints, not by this package).
- **Kish, L. (1965).** *Survey Sampling.* Wiley. The effective sample size
  `(Σw)² / Σw²` used for weighted index summaries. A book with no DOI; *not verified through
  Crossref.*

## Unverified attribution

- D-037 attributes the 08:00–20:30 daytime window of the upstream footprint stack to
  "Volk 2023". Two 2023 papers led by J. M. Volk exist: Data in Brief 109274,
  <https://doi.org/10.1016/j.dib.2023.109274>, and Agricultural and Forest Meteorology
  109307, <https://doi.org/10.1016/j.agrformet.2023.109307>. Which one, and which passage,
  is not verified. The window is an upstream setting of the user's footprint workflow, not
  a method of this package.

## Software

- **flux-data-qaqc**: <https://github.com/inkenbrandt/flux-data-qaqc>. Produces the
  station daily exports. It is not a dependency, and its version is recorded only as
  declared in `upstream:`.
- **fluxfootprints**: <https://github.com/inkenbrandt/footprints>, GPL-3.0. Produces the
  footprints. It is an optional, separately installed dependency used only for
  representativeness labels (D-056).
