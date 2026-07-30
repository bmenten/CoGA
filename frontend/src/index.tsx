import React, { Suspense, lazy } from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router';
import { QueryClientProvider } from '@tanstack/react-query';
import Layout from './components/Layout';
import RequireAuth from './components/RequireAuth';
import RequireAdmin from './components/RequireAdmin';
import SessionRedirect from './components/SessionRedirect';
import PageState from './components/PageState';
import { createAppQueryClient } from './lib/queryClient';
import './styles/theme.css';
import './index.css';

const queryClient = createAppQueryClient();

const LoginPage = lazy(() => import('./pages/auth/LoginPage'));
const SignupPage = lazy(() => import('./pages/auth/SignupPage'));
const Dashboard = lazy(() => import('./pages/dashboard/Dashboard'));
const FamilyBuilderPage = lazy(
  () => import('./pages/dashboard/FamilyBuilderPage')
);
const PackageImportPage = lazy(
  () => import('./pages/dashboard/PackageImportPage')
);
const FamiliesPage = lazy(() => import('./pages/families/FamiliesPage'));
const FamilyDetailPage = lazy(
  () => import('./pages/families/FamilyDetailPage')
);
const GenomeOverviewPage = lazy(
  () => import('./pages/genome/GenomeOverviewPage')
);
const ChromosomeViewPage = lazy(
  () => import('./pages/genome/ChromosomeViewPage')
);
const CircosPlotPage = lazy(() => import('./pages/genome/CircosPlotPage'));
const CnvDetailsPage = lazy(() => import('./pages/genome/CnvDetailsPage'));
const ClinicalCnvExplorerPage = lazy(
  () => import('./pages/cnv-explorer/ClinicalCnvExplorerPage'),
);
const SampleUpload = lazy(() => import('./pages/uploads/SampleUpload'));
const FamilyStructuralVariantsPage = lazy(
  () => import('./pages/families/FamilyStructuralVariantsPage')
);
const FamilyVariantSummaryPage = lazy(
  () => import('./pages/families/FamilyVariantSummaryPage')
);
const FamilyRoiMarkersPage = lazy(
  () => import('./pages/families/FamilyRoiMarkersPage')
);
const FamilyReportPage = lazy(
  () => import('./pages/families/FamilyReportPage')
);
const FamilySampleQcPage = lazy(
  () => import('./pages/families/FamilySampleQcPage')
);
const FamilySmallVariantsPage = lazy(
  () => import('./pages/families/FamilySmallVariantsPage')
);
const FamilyRepeatExpansionsPage = lazy(
  () => import('./pages/families/FamilyRepeatExpansionsPage')
);
const FamilyParaphasePage = lazy(
  () => import('./pages/families/FamilyParaphasePage')
);
const FamilyMitoDNAAnalysisPage = lazy(
  () => import('./pages/families/FamilyMitoDNAAnalysisPage')
);
const FamilyNiptPage = lazy(
  () => import('./pages/families/FamilyNiptPage')
);
const FamilyNiptReportPage = lazy(
  () => import('./pages/families/FamilyNiptReportPage')
);
const GeneInfoPage = lazy(() => import('./pages/genes/GeneInfoPage'));
const GlobalSmallVariantExplorerPage = lazy(
  () => import('./pages/variant-explorer/GlobalSmallVariantExplorerPage'),
);
const HpoTermsPage = lazy(() => import('./pages/phenotypes/HpoTermsPage'));
const GenePanelsPage = lazy(() => import('./pages/panels/GenePanelsPage'));
const GenePanelDetailPage = lazy(
  () => import('./pages/panels/GenePanelDetailPage')
);
const AdminDashboardPage = lazy(
  () => import('./pages/admin/AdminDashboardPage')
);
const UserListPage = lazy(() => import('./pages/admin/UserListPage'));
const DataManagementPage = lazy(
  () => import('./pages/admin/DataManagementPage')
);
const AdminClickhouseManagementPage = lazy(
  () => import('./pages/admin/AdminClickhouseManagementPage')
);
const AdminPresetFiltersPage = lazy(
  () => import('./pages/admin/AdminPresetFiltersPage')
);
const AdminVariantTagsPage = lazy(
  () => import('./pages/admin/AdminVariantTagsPage')
);
const AdminQcThresholdsPage = lazy(
  () => import('./pages/admin/AdminQcThresholdsPage')
);
const AdminFamilyStatusesPage = lazy(
  () => import('./pages/admin/AdminFamilyStatusesPage')
);
const AdminAuditLogsPage = lazy(
  () => import('./pages/admin/AdminAuditLogsPage')
);
const GeneReferenceAdminPage = lazy(
  () => import('./pages/admin/GeneReferenceAdminPage')
);
const HpoTerminologyAdminPage = lazy(
  () => import('./pages/admin/HpoTerminologyAdminPage')
);
const MonarchDataAdminPage = lazy(
  () => import('./pages/admin/MonarchDataAdminPage')
);
const ProjectsPage = lazy(() => import('./pages/projects/ProjectsPage'));
const ReferenceCatalogPage = lazy(
  () => import('./pages/reference/ReferenceCatalogPage')
);
const SettingsPage = lazy(() => import('./pages/settings/SettingsPage'));
const UserGuidePage = lazy(() => import('./pages/docs/UserGuidePage'));
const ReferenceDocPage = lazy(() => import('./pages/docs/ReferenceDocPage'));
const NewFeaturesPage = lazy(() => import('./pages/product/NewFeaturesPage'));
const FamilyIgvPage = lazy(() => import('./pages/families/FamilyIgvPage'));
const NotFound = lazy(() => import('./pages/NotFound'));

const RouteFallback: React.FC = () => (
  <PageState
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
            <Route path="/signup" element={routeElement(<SignupPage />)} />
            <Route element={<RequireAuth />}>
              <Route path="/dashboard" element={routeElement(<Dashboard />)} />
              <Route
                path="/family-builder"
                element={routeElement(<FamilyBuilderPage />)}
              />
              <Route
                path="/family-intake"
                element={<Navigate to="/family-builder" replace />}
              />
              <Route
                path="/families"
                element={routeElement(<FamiliesPage />)}
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
                path="/families/:familyId/igv"
                element={routeElement(<FamilyIgvPage />)}
              />
              <Route path="/genes" element={routeElement(<GeneInfoPage />)} />
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
              <Route element={<RequireAdmin />}>
                <Route
                  path="/admin"
                  element={routeElement(<AdminDashboardPage />)}
                />
                <Route
                  path="/admin/reference/assemblies"
                  element={routeElement(<ReferenceCatalogPage />)}
                />
                <Route
                  path="/admin/reference/gene-panels"
                  element={routeElement(<GenePanelsPage />)}
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
                  path="/admin/hpo"
                  element={routeElement(<HpoTerminologyAdminPage />)}
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
                  path="/package-import"
                  element={routeElement(<PackageImportPage />)}
                />
                <Route
                  path="/admin/data/upload"
                  element={routeElement(<PackageImportPage />)}
                />
                <Route
                  path="/admin/upload"
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
                  path="/admin/operations/clickhouse"
                  element={routeElement(<AdminClickhouseManagementPage />)}
                />
                <Route
                  path="/admin/operations/variants"
                  element={<Navigate to="/admin/operations/clickhouse" replace />}
                />
                <Route
                  path="/admin/monitoring/audit-logs"
                  element={routeElement(<AdminAuditLogsPage />)}
                />
                <Route
                  path="/admin/users"
                  element={routeElement(<UserListPage />)}
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
                  path="/admin/data/qc-thresholds"
                  element={routeElement(<AdminQcThresholdsPage />)}
                />
                <Route
                  path="/admin/data/clickhouse"
                  element={routeElement(<AdminClickhouseManagementPage />)}
                />
                <Route
                  path="/admin/data/presets"
                  element={routeElement(<AdminPresetFiltersPage />)}
                />
                <Route
                  path="/admin/data/tags"
                  element={routeElement(<AdminVariantTagsPage />)}
                />
                <Route
                  path="/admin/data/logs"
                  element={routeElement(<AdminAuditLogsPage />)}
                />
                <Route
                  path="/admin/gene-reference"
                  element={routeElement(<GeneReferenceAdminPage />)}
                />
                <Route
                  path="/projects"
                  element={routeElement(<ProjectsPage />)}
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
