import React, { Suspense } from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Routes, Route } from 'react-router';
import { QueryClientProvider } from '@tanstack/react-query';
import Layout from './components/Layout';
import RequireAuth from './components/RequireAuth';
import RequireAdmin from './components/RequireAdmin';
import SessionRedirect from './components/SessionRedirect';
import PageState from './components/PageState';
import { lazyPage } from './lib/lazyPage';
import { createAppQueryClient } from './lib/queryClient';
import './styles/theme.css';
import './index.css';

const queryClient = createAppQueryClient();

const LoginPage = lazyPage(() => import('./pages/auth/LoginPage'));
const SignupPage = lazyPage(() => import('./pages/auth/SignupPage'));
const Dashboard = lazyPage(() => import('./pages/dashboard/Dashboard'));
const FamilyBuilderPage = lazyPage(
  () => import('./pages/dashboard/FamilyBuilderPage')
);
const PackageImportPage = lazyPage(
  () => import('./pages/dashboard/PackageImportPage')
);
const FamilyDetailPage = lazyPage(
  () => import('./pages/families/FamilyDetailPage')
);
const GenomeOverviewPage = lazyPage(
  () => import('./pages/genome/GenomeOverviewPage')
);
const ChromosomeViewPage = lazyPage(
  () => import('./pages/genome/ChromosomeViewPage')
);
const CircosPlotPage = lazyPage(() => import('./pages/genome/CircosPlotPage'));
const CnvDetailsPage = lazyPage(() => import('./pages/genome/CnvDetailsPage'));
const ClinicalCnvExplorerPage = lazyPage(
  () => import('./pages/cnv-explorer/ClinicalCnvExplorerPage'),
);
const SampleUpload = lazyPage(() => import('./pages/uploads/SampleUpload'));
const FamilyStructuralVariantsPage = lazyPage(
  () => import('./pages/families/FamilyStructuralVariantsPage')
);
const FamilyVariantSummaryPage = lazyPage(
  () => import('./pages/families/FamilyVariantSummaryPage')
);
const FamilyRoiMarkersPage = lazyPage(
  () => import('./pages/families/FamilyRoiMarkersPage')
);
const FamilyReportPage = lazyPage(
  () => import('./pages/families/FamilyReportPage')
);
const FamilySampleQcPage = lazyPage(
  () => import('./pages/families/FamilySampleQcPage')
);
const FamilySmallVariantsPage = lazyPage(
  () => import('./pages/families/FamilySmallVariantsPage')
);
const FamilyRepeatExpansionsPage = lazyPage(
  () => import('./pages/families/FamilyRepeatExpansionsPage')
);
const FamilyParaphasePage = lazyPage(
  () => import('./pages/families/FamilyParaphasePage')
);
const FamilyMitoDNAAnalysisPage = lazyPage(
  () => import('./pages/families/FamilyMitoDNAAnalysisPage')
);
const FamilyNiptPage = lazyPage(
  () => import('./pages/families/FamilyNiptPage')
);
const FamilyNiptReportPage = lazyPage(
  () => import('./pages/families/FamilyNiptReportPage')
);
const FamilyNiptCoveragePage = lazyPage(
  () => import('./pages/families/FamilyNiptCoveragePage')
);
const GeneInfoPage = lazyPage(() => import('./pages/genes/GeneInfoPage'));
const ReportCodeLookupPage = lazyPage(() => import('./pages/reports/ReportCodeLookupPage'));
const GlobalSmallVariantExplorerPage = lazyPage(
  () => import('./pages/variant-explorer/GlobalSmallVariantExplorerPage'),
);
const HpoTermsPage = lazyPage(() => import('./pages/phenotypes/HpoTermsPage'));
const GenePanelsPage = lazyPage(() => import('./pages/panels/GenePanelsPage'));
const GenePanelDetailPage = lazyPage(
  () => import('./pages/panels/GenePanelDetailPage')
);
const AdminDashboardPage = lazyPage(
  () => import('./pages/admin/AdminDashboardPage')
);
const UserListPage = lazyPage(() => import('./pages/admin/UserListPage'));
const DataManagementPage = lazyPage(
  () => import('./pages/admin/DataManagementPage')
);
const AdminClickhouseManagementPage = lazyPage(
  () => import('./pages/admin/AdminClickhouseManagementPage')
);
const AdminPresetFiltersPage = lazyPage(
  () => import('./pages/admin/AdminPresetFiltersPage')
);
const AdminVariantTagsPage = lazyPage(
  () => import('./pages/admin/AdminVariantTagsPage')
);
const AdminQcThresholdsPage = lazyPage(
  () => import('./pages/admin/AdminQcThresholdsPage')
);
const AdminFamilyStatusesPage = lazyPage(
  () => import('./pages/admin/AdminFamilyStatusesPage')
);
const AdminAuditLogsPage = lazyPage(
  () => import('./pages/admin/AdminAuditLogsPage')
);
const GeneReferenceAdminPage = lazyPage(
  () => import('./pages/admin/GeneReferenceAdminPage')
);
const HpoTerminologyAdminPage = lazyPage(
  () => import('./pages/admin/HpoTerminologyAdminPage')
);
const MonarchDataAdminPage = lazyPage(
  () => import('./pages/admin/MonarchDataAdminPage')
);
const AdminRepeatLociPage = lazyPage(
  () => import('./pages/admin/AdminRepeatLociPage')
);
const ProjectsPage = lazyPage(() => import('./pages/projects/ProjectsPage'));
const ReferenceCatalogPage = lazyPage(
  () => import('./pages/reference/ReferenceCatalogPage')
);
const SettingsPage = lazyPage(() => import('./pages/settings/SettingsPage'));
const UserGuidePage = lazyPage(() => import('./pages/docs/UserGuidePage'));
const ReferenceDocPage = lazyPage(() => import('./pages/docs/ReferenceDocPage'));
const NewFeaturesPage = lazyPage(() => import('./pages/product/NewFeaturesPage'));
const FamilyIgvPage = lazyPage(() => import('./pages/families/FamilyIgvPage'));
const NotFound = lazyPage(() => import('./pages/NotFound'));

const RouteFallback: React.FC = () => (
  <PageState
    loading
    kicker="Workspace"
    title="Loading workspace"
    message="Preparing the requested view."
    narrow
  />
);

const routeElement = (element: React.ReactElement) => (
  <Suspense fallback={<RouteFallback />}>{element}</Suspense>
);

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route
              path="/"
              element={
                <SessionRedirect
                  authenticatedTo="/dashboard"
                  unauthenticatedTo="/login"
                />
              }
            />
            <Route path="/login" element={routeElement(<LoginPage />)} />
            {/* The emergency administrator's password sign-in beside Microsoft sign-in. */}
            <Route path="/login/emergency" element={routeElement(<LoginPage emergency />)} />
            <Route path="/signup" element={routeElement(<SignupPage />)} />
            <Route element={<RequireAuth />}>
              <Route path="/dashboard" element={routeElement(<Dashboard />)} />
              <Route
                path="/family-builder"
                element={routeElement(<FamilyBuilderPage />)}
              />
              <Route
                path="/families/:familyId"
                element={routeElement(<FamilyDetailPage />)}
              />
              <Route
                path="/families/:familyId/genome"
                element={routeElement(<GenomeOverviewPage />)}
              />
              <Route
                path="/families/:familyId/chromosome/:chrom"
                element={routeElement(<ChromosomeViewPage />)}
              />
              <Route
                path="/families/:familyId/circos"
                element={routeElement(<CircosPlotPage />)}
              />
              <Route
                path="/families/:familyId/structural-variants"
                element={routeElement(<FamilyStructuralVariantsPage />)}
              />
              <Route
                path="/families/:familyId/small-variants"
                element={routeElement(<FamilySmallVariantsPage />)}
              />
              <Route
                path="/families/:familyId/roi-markers"
                element={routeElement(<FamilyRoiMarkersPage />)}
              />
              <Route
                path="/families/:familyId/report"
                element={routeElement(<FamilyReportPage />)}
              />
              <Route
                path="/families/:familyId/qc"
                element={routeElement(<FamilySampleQcPage />)}
              />
              <Route
                path="/families/:familyId/variant-summary"
                element={routeElement(<FamilyVariantSummaryPage />)}
              />
              <Route
                path="/families/:familyId/repeat-expansions"
                element={routeElement(<FamilyRepeatExpansionsPage />)}
              />
              <Route
                path="/families/:familyId/paraphase"
                element={routeElement(<FamilyParaphasePage />)}
              />
              <Route
                path="/families/:familyId/mitochondrial-dna"
                element={routeElement(<FamilyMitoDNAAnalysisPage />)}
              />
              <Route
                path="/families/:familyId/nipt"
                element={routeElement(<FamilyNiptPage />)}
              />
              <Route
                path="/families/:familyId/nipt/report"
                element={routeElement(<FamilyNiptReportPage />)}
              />
              <Route
                path="/families/:familyId/nipt/coverage"
                element={routeElement(<FamilyNiptCoveragePage />)}
              />
              <Route
                path="/families/:familyId/igv"
                element={routeElement(<FamilyIgvPage />)}
              />
              <Route path="/genes" element={routeElement(<GeneInfoPage />)} />
              <Route path="/report-codes" element={routeElement(<ReportCodeLookupPage />)} />
              <Route
                path="/report-codes/:code"
                element={routeElement(<ReportCodeLookupPage />)}
              />
              <Route
                path="/variant-explorer"
                element={routeElement(<GlobalSmallVariantExplorerPage />)}
              />
              <Route path="/hpo" element={routeElement(<HpoTermsPage />)} />
              <Route
                path="/settings"
                element={routeElement(<SettingsPage />)}
              />
              <Route
                path="/new-features"
                element={routeElement(<NewFeaturesPage />)}
              />
              <Route
                path="/reference-data"
                element={routeElement(<ReferenceCatalogPage />)}
              />
              <Route
                path="/cnv-explorer"
                element={routeElement(<ClinicalCnvExplorerPage />)}
              />
              <Route
                path="/cnv-details/:cnvId"
                element={routeElement(<CnvDetailsPage />)}
              />
              <Route
                path="/panels"
                element={routeElement(<GenePanelsPage />)}
              />
              <Route
                path="/panels/:panelId"
                element={routeElement(<GenePanelDetailPage />)}
              />
              <Route path="/docs" element={routeElement(<UserGuidePage />)} />
              <Route path="/docs/reference/:slug" element={routeElement(<ReferenceDocPage />)} />
              {/* A superuser's workspaces: the admin dashboard, with the configuration the analyses
                  read and package import. The server is the gate (get_current_superuser). */}
              <Route element={<RequireAdmin superuser />}>
                <Route
                  path="/admin"
                  element={routeElement(<AdminDashboardPage />)}
                />
                <Route
                  path="/admin/reference/gene-panels"
                  element={routeElement(<GenePanelsPage />)}
                />
                <Route
                  path="/package-import"
                  element={routeElement(<PackageImportPage />)}
                />
                <Route
                  path="/admin/data/upload"
                  element={routeElement(<PackageImportPage />)}
                />
                <Route
                  path="/admin/variants/tags"
                  element={routeElement(<AdminVariantTagsPage />)}
                />
                <Route
                  path="/admin/variants/presets"
                  element={routeElement(<AdminPresetFiltersPage />)}
                />
                <Route
                  path="/admin/data/qc-thresholds"
                  element={routeElement(<AdminQcThresholdsPage />)}
                />
              </Route>
              {/* The rest of the administration: an admin's alone (get_current_admin_user). */}
              <Route element={<RequireAdmin />}>
                <Route
                  path="/admin/reference/assemblies"
                  element={routeElement(<ReferenceCatalogPage />)}
                />
                <Route
                  path="/admin/reference/gene-reference"
                  element={routeElement(<GeneReferenceAdminPage />)}
                />
                <Route
                  path="/admin/reference/hpo"
                  element={routeElement(<HpoTerminologyAdminPage />)}
                />
                <Route
                  path="/admin/reference/monarch"
                  element={routeElement(<MonarchDataAdminPage />)}
                />
                <Route
                  path="/admin/reference/repeat-loci"
                  element={routeElement(<AdminRepeatLociPage />)}
                />
                <Route
                  path="/admin/access/users"
                  element={routeElement(<UserListPage />)}
                />
                <Route
                  path="/admin/access/projects"
                  element={routeElement(<ProjectsPage />)}
                />
                <Route
                  path="/admin/data/families"
                  element={routeElement(<DataManagementPage />)}
                />
                <Route
                  path="/admin/operations/clickhouse"
                  element={routeElement(<AdminClickhouseManagementPage />)}
                />
                <Route
                  path="/admin/monitoring/audit-logs"
                  element={routeElement(<AdminAuditLogsPage />)}
                />
                <Route
                  path="/admin/data"
                  element={routeElement(<DataManagementPage />)}
                />
                <Route
                  path="/admin/data/families/:familyId/structure"
                  element={routeElement(<FamilyDetailPage editable />)}
                />
                <Route
                  path="/admin/data/family-statuses"
                  element={routeElement(<AdminFamilyStatusesPage />)}
                />
                <Route
                  path="/upload-data"
                  element={routeElement(<SampleUpload />)}
                />
              </Route>
              <Route path="*" element={routeElement(<NotFound />)} />
            </Route>
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
);
