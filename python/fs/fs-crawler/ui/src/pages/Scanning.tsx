import React, { useState, useEffect } from 'react'
import {
  Box,
  Typography,
  Card,
  CardContent,
  Button,
  Grid,
  LinearProgress,
  Chip,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  FormControlLabel,
  Switch,
  Alert,
  IconButton,
  Tooltip,
  CardHeader,
  Avatar,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Skeleton,
  LinearProgressProps,
  Fab,
  Zoom,
} from '@mui/material'
import {
  PlayArrow as PlayIcon,
  Stop as StopIcon,
  Refresh as RefreshIcon,
  Scanner as ScannerIcon,
  Folder as FolderIcon,
  Settings as SettingsIcon,
  Info as InfoIcon,
  ExpandMore as ExpandMoreIcon,
  CheckCircle as CheckCircleIcon,
  Error as ErrorIcon,
  Schedule as ScheduleIcon,
  Storage as StorageIcon,
  TrendingUp as TrendingUpIcon,
  ExpandLess as ExpandLessIcon,
  ExpandMore as ExpandMoreIcon2,
} from '@mui/icons-material'
import { useForm, Controller } from 'react-hook-form'
import { standardSchemaResolver } from '@hookform/resolvers/standard-schema'
import { z } from 'zod'
import { useScanStatus, useStartScan, useStopScan, useLibraries } from '@/hooks/useApi'
import { formatDistanceToNow } from 'date-fns'
import type { StartScanForm } from '@/types/api'

const scanFormSchema = z.object({
  path: z.string().optional(),
  deep_scan: z.boolean().default(false),
})

type ScanFormData = z.infer<typeof scanFormSchema>

const Scanning: React.FC = () => {
  const [startScanDialogOpen, setStartScanDialogOpen] = useState(false)
  const [expandedScan, setExpandedScan] = useState<string | null>(null)

  const { data: scanStatus, isLoading: scanStatusLoading, refetch } = useScanStatus()
  const { data: libraries } = useLibraries()
  const startScanMutation = useStartScan()
  const stopScanMutation = useStopScan()

  const { control, handleSubmit, reset, watch } = useForm<ScanFormData>({
    resolver: standardSchemaResolver(scanFormSchema),
    defaultValues: {
      deep_scan: false,
    },
  })

  const watchedPath = watch('path')
  const watchedDeepScan = watch('deep_scan')

  const handleStartScanDialog = () => {
    reset()
    setStartScanDialogOpen(true)
  }

  const handleCloseScanDialog = () => {
    setStartScanDialogOpen(false)
    reset()
  }

  const handleSubmitScan = (data: ScanFormData) => {
    const scanData: StartScanForm = {}
    if (data.path) scanData.path = data.path
    if (data.deep_scan) scanData.deep_scan = data.deep_scan

    startScanMutation.mutate(scanData, {
      onSuccess: () => {
        handleCloseScanDialog()
      },
    })
  }

  const handleQuickScan = (path?: string, deepScan: boolean = false) => {
    const scanData: StartScanForm = {}
    if (path) scanData.path = path
    if (deepScan) scanData.deep_scan = deepScan

    startScanMutation.mutate(scanData)
  }

  const formatNumber = (num: string | number) => {
    const n = typeof num === 'string' ? parseInt(num) : num
    return new Intl.NumberFormat().format(n)
  }

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'running': return 'warning'
      case 'completed': return 'success'
      case 'failed': return 'error'
      default: return 'default'
    }
  }

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'running': return <ScannerIcon className="spin" />
      case 'completed': return <CheckCircleIcon />
      case 'failed': return <ErrorIcon />
      default: return <ScannerIcon />
    }
  }

  const hasActiveScans = scanStatus && scanStatus.active_scans > 0
  const activeScanOperations = scanStatus?.scans.filter(scan => scan.status === 'running') || []

  // Toggle scan expansion
  const toggleScanExpansion = (path: string) => {
    setExpandedScan(expandedScan === path ? null : path)
  }

  // Set up polling for real-time updates
  useEffect(() => {
    if (hasActiveScans) {
      const interval = setInterval(() => {
        refetch();
      }, 2000); // Refresh every 2 seconds when scans are active

      return () => clearInterval(interval);
    }
  }, [hasActiveScans, refetch]);

  return (
    <Box sx={{ width: '100%' }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
        <Typography variant="h4" sx={{ fontWeight: 600 }}>
          Scanning
        </Typography>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button
            variant="outlined"
            startIcon={<RefreshIcon />}
            onClick={() => refetch()}
            sx={{ borderRadius: 2, px: 2, py: 1 }}
          >
            Refresh
          </Button>
          <Button
            variant="contained"
            startIcon={hasActiveScans ? <StopIcon /> : <PlayIcon />}
            onClick={() => hasActiveScans ? stopScanMutation.mutate() : handleStartScanDialog()}
            disabled={startScanMutation.isPending || stopScanMutation.isPending}
            color={hasActiveScans ? 'error' : 'primary'}
            sx={{
              borderRadius: 2,
              px: 3,
              py: 1.2,
              fontWeight: 500,
              boxShadow: '0px 2px 8px rgba(0, 0, 0, 0.1)',
              '&:hover': {
                boxShadow: '0px 4px 12px rgba(0, 0, 0, 0.15)',
              }
            }}
          >
            {hasActiveScans ? 'Stop All Scans' : 'Start Scan'}
          </Button>
        </Box>
      </Box>

      {/* Scan Status Overview */}
      <Grid container spacing={3} sx={{ mb: 4 }}>
        <Grid item xs={12} md={4}>
          <Card
            sx={{
              height: '100%',
              borderRadius: 3,
              display: 'flex',
              flexDirection: 'column',
              transition: 'transform 0.3s ease, box-shadow 0.3s ease',
              '&:hover': {
                transform: 'translateY(-4px)',
                boxShadow: '0px 6px 16px rgba(0, 0, 0, 0.12)',
              },
              borderLeft: hasActiveScans ? '4px solid' : '4px solid transparent',
              borderLeftColor: hasActiveScans ? 'warning.main' : 'transparent',
            }}
          >
            <CardHeader
              avatar={
                <Avatar sx={{ bgcolor: hasActiveScans ? 'warning.main' : 'success.main' }}>
                  <ScannerIcon />
                </Avatar>
              }
              title={
                <Typography variant="h6" sx={{ fontWeight: 600 }}>
                  Active Scans
                </Typography>
              }
              sx={{ pb: 1 }}
            />
            <CardContent sx={{ pt: 0, flex: 1 }}>
              {scanStatusLoading ? (
                <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                  <Skeleton variant="text" height={50} width="60%" />
                  <Skeleton variant="text" height={20} width="80%" sx={{ mt: 1 }} />
                </Box>
              ) : (
                <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                  <Typography
                    variant="h3"
                    color={hasActiveScans ? 'warning.main' : 'success.main'}
                    sx={{ fontWeight: 700, mb: 1 }}
                  >
                    {scanStatus?.active_scans || 0}
                  </Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                    Currently Running
                  </Typography>
                  <Box sx={{ mt: 'auto' }}>
                    <Typography variant="caption" color="text.secondary">
                      {activeScanOperations.length > 0
                        ? `${activeScanOperations.length} active operations`
                        : 'No active scans'}
                    </Typography>
                  </Box>
                </Box>
              )}
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} md={4}>
          <Card
            sx={{
              height: '100%',
              borderRadius: 3,
              display: 'flex',
              flexDirection: 'column',
              transition: 'transform 0.3s ease, box-shadow 0.3s ease',
              '&:hover': {
                transform: 'translateY(-4px)',
                boxShadow: '0px 6px 16px rgba(0, 0, 0, 0.12)',
              },
              borderLeft: '4px solid',
              borderLeftColor: 'primary.main',
            }}
          >
            <CardHeader
              avatar={
                <Avatar sx={{ bgcolor: 'primary.main' }}>
                  <FolderIcon />
                </Avatar>
              }
              title={
                <Typography variant="h6" sx={{ fontWeight: 600 }}>
                  Library Paths
                </Typography>
              }
              sx={{ pb: 1 }}
            />
            <CardContent sx={{ pt: 0, flex: 1 }}>
              {scanStatusLoading ? (
                <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                  <Skeleton variant="text" height={50} width="60%" />
                  <Skeleton variant="text" height={20} width="80%" sx={{ mt: 1 }} />
                </Box>
              ) : (
                <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                  <Typography variant="h3" color="primary" sx={{ fontWeight: 700, mb: 1 }}>
                    {libraries?.length || 0}
                  </Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                    Configured Paths
                  </Typography>
                  <Box sx={{ mt: 'auto' }}>
                    {libraries && (
                      <Typography variant="caption" color="text.secondary">
                        {libraries.filter(lib => lib.scan_enabled).length} enabled for scanning
                      </Typography>
                    )}
                  </Box>
                </Box>
              )}
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} md={4}>
          <Card
            sx={{
              height: '100%',
              borderRadius: 3,
              display: 'flex',
              flexDirection: 'column',
              transition: 'transform 0.3s ease, box-shadow 0.3s ease',
              '&:hover': {
                transform: 'translateY(-4px)',
                boxShadow: '0px 6px 16px rgba(0, 0, 0, 0.12)',
              },
              borderLeft: '4px solid',
              borderLeftColor: 'info.main',
            }}
          >
            <CardHeader
              avatar={
                <Avatar sx={{ bgcolor: 'info.main' }}>
                  <TrendingUpIcon />
                </Avatar>
              }
              title={
                <Typography variant="h6" sx={{ fontWeight: 600 }}>
                  Total Operations
                </Typography>
              }
              sx={{ pb: 1 }}
            />
            <CardContent sx={{ pt: 0, flex: 1 }}>
              {scanStatusLoading ? (
                <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                  <Skeleton variant="text" height={50} width="60%" />
                  <Skeleton variant="text" height={20} width="80%" sx={{ mt: 1 }} />
                </Box>
              ) : (
                <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                  <Typography variant="h3" color="info" sx={{ fontWeight: 700, mb: 1 }}>
                    {scanStatus?.scans.length || 0}
                  </Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                    Recent Scans
                  </Typography>
                  <Box sx={{ mt: 'auto' }}>
                    <Typography variant="caption" color="text.secondary">
                      {scanStatus?.scans.filter(s => s.status === 'completed').length || 0} completed
                    </Typography>
                  </Box>
                </Box>
              )}
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* Quick Actions */}
      <Card sx={{ borderRadius: 3, mb: 4, overflow: 'hidden' }}>
        <CardHeader
          avatar={
            <Avatar sx={{ bgcolor: 'primary.main' }}>
              <ScheduleIcon />
            </Avatar>
          }
          title={
            <Typography variant="h6" sx={{ fontWeight: 600 }}>
              Quick Actions
            </Typography>
          }
          sx={{ pb: 1 }}
        />
        <CardContent sx={{ pt: 0 }}>
          <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
            <Button
              variant="outlined"
              startIcon={<PlayIcon />}
              onClick={() => handleQuickScan()}
              disabled={startScanMutation.isPending || hasActiveScans}
              sx={{
                borderRadius: 2,
                px: 3,
                py: 1.2,
                fontWeight: 500,
              }}
            >
              Scan All Libraries
            </Button>
            <Button
              variant="outlined"
              startIcon={<ScannerIcon />}
              onClick={() => handleQuickScan(undefined, true)}
              disabled={startScanMutation.isPending || hasActiveScans}
              sx={{
                borderRadius: 2,
                px: 3,
                py: 1.2,
                fontWeight: 500,
              }}
            >
              Deep Scan All
            </Button>
            {libraries && libraries.length > 0 && (
              <>
                {libraries.slice(0, 3).map((library) => (
                  <Button
                    key={library.id}
                    variant="outlined"
                    size="small"
                    startIcon={<FolderIcon />}
                    onClick={() => handleQuickScan(library.path)}
                    disabled={startScanMutation.isPending || hasActiveScans}
                    sx={{
                      borderRadius: 2,
                      px: 2,
                      py: 1,
                      fontWeight: 500,
                    }}
                  >
                    Scan {library.name || library.path.split('/').pop()}
                  </Button>
                ))}
              </>
            )}
          </Box>
        </CardContent>
      </Card>

      {/* Active Scans Progress */}
      {hasActiveScans && (
        <Card sx={{ borderRadius: 3, mb: 4, overflow: 'hidden' }}>
          <CardHeader
            avatar={
              <Avatar sx={{ bgcolor: 'warning.main' }}>
                <ScannerIcon className="pulse" />
              </Avatar>
            }
            title={
              <Typography variant="h6" sx={{ fontWeight: 600 }}>
                Active Scan Progress
              </Typography>
            }
            sx={{ pb: 1 }}
          />
          <CardContent sx={{ pt: 0 }}>
            {activeScanOperations.map((scan, index) => (
              <Card
                key={index}
                sx={{
                  mb: 3,
                  p: 2,
                  borderRadius: 2,
                  backgroundColor: 'action.hover',
                  borderLeft: '3px solid',
                  borderLeftColor: 'warning.main',
                }}
              >
                <Box
                  sx={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    mb: 1,
                    cursor: 'pointer',
                  }}
                  onClick={() => toggleScanExpansion(scan.path)}
                >
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <IconButton size="small">
                      {expandedScan === scan.path ? <ExpandLessIcon /> : <ExpandMoreIcon2 />}
                    </IconButton>
                    <Typography variant="body1" sx={{ fontFamily: 'monospace', fontWeight: 500 }}>
                      {scan.path}
                    </Typography>
                  </Box>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <Typography variant="body2" color="text.secondary">
                      {formatNumber(scan.files_processed)} files processed
                    </Typography>
                    <Chip
                      icon={getStatusIcon(scan.status)}
                      label={scan.status}
                      size="small"
                      color={getStatusColor(scan.status)}
                      sx={{ borderRadius: 16 }}
                    />
                  </Box>
                </Box>

                {(scan.current_directory || typeof scan.progress_percentage === 'number') && (
                  <Zoom in={expandedScan === scan.path || expandedScan === null}>
                    <Box
                      sx={{
                        pl: 3,
                        display: expandedScan === scan.path || expandedScan === null ? 'block' : 'none',
                        mt: 1,
                      }}
                    >
                      {scan.current_directory && (
                        <Box sx={{ mb: 1 }}>
                          <Typography variant="caption" color="text.secondary">
                            <strong>Currently scanning:</strong> {scan.current_directory}
                          </Typography>
                        </Box>
                      )}
                      {typeof scan.progress_percentage === 'number' && (
                        <Box sx={{ mb: 1 }}>
                          <LinearProgress
                            variant="determinate"
                            value={scan.progress_percentage}
                            sx={{ height: 8, borderRadius: 4 }}
                          />
                          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', textAlign: 'right' }}>
                            {scan.progress_percentage.toFixed(1)}%
                          </Typography>
                        </Box>
                      )}
                      <Typography variant="caption" color="text.secondary">
                        Started: {scan.started_at ? `${formatDistanceToNow(new Date(scan.started_at))} ago` : 'N/A'}
                        {scan.deep_scan === 'true' && ' • Deep Scan'}
                      </Typography>
                    </Box>
                  </Zoom>
                )}
              </Card>
            ))}
          </CardContent>
        </Card>
      )}

      {/* Scan History */}
      <Card sx={{ borderRadius: 3, overflow: 'hidden' }}>
        <CardHeader
          avatar={
            <Avatar sx={{ bgcolor: 'info.main' }}>
              <StorageIcon />
            </Avatar>
          }
          title={
            <Typography variant="h6" sx={{ fontWeight: 600 }}>
              Recent Scan Operations
            </Typography>
          }
          sx={{ pb: 1 }}
        />
        <CardContent sx={{ pt: 0 }}>
          {scanStatusLoading ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', py: 4 }}>
              <LinearProgress sx={{ width: '100%' }} />
            </Box>
          ) : scanStatus && scanStatus.scans.length > 0 ? (
            <TableContainer component={Paper} variant="outlined" sx={{ borderRadius: 2, boxShadow: 'none' }}>
              <Table>
                <TableHead>
                  <TableRow sx={{ backgroundColor: 'background.paper' }}>
                    <TableCell>Path</TableCell>
                    <TableCell>Status</TableCell>
                    <TableCell>Files Processed</TableCell>
                    <TableCell>Progress</TableCell>
                    <TableCell>Current Directory</TableCell>
                    <TableCell>Started</TableCell>
                    <TableCell>Type</TableCell>
                    <TableCell>Actions</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {scanStatus.scans.map((scan, index) => (
                    <TableRow
                      key={index}
                      sx={{
                        '&:nth-of-type(odd)': { backgroundColor: 'action.hover' },
                        '&:last-child td, &:last-child th': { border: 0 },
                        transition: 'all 0.3s ease',
                        '&:hover': {
                          backgroundColor: 'action.selected',
                          transform: 'translateY(-1px)',
                          boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
                        }
                      }}
                    >
                      <TableCell>
                        <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                          {scan.path}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Chip
                          icon={getStatusIcon(scan.status)}
                          label={scan.status}
                          size="small"
                          color={getStatusColor(scan.status)}
                          sx={{ borderRadius: 16 }}
                        />
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2">
                          {formatNumber(scan.files_processed)}
                          {typeof scan.progress_percentage === 'number' && (
                            <Typography variant="caption" color="text.secondary" component="div">
                              ({scan.progress_percentage}%)
                            </Typography>
                          )}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        {scan.current_directory ? (
                          <Typography variant="body2" sx={{ fontFamily: 'monospace', fontSize: '0.8rem' }}>
                            {scan.current_directory.split('/').pop() || scan.current_directory}
                          </Typography>
                        ) : (
                          <Typography variant="body2" color="text.secondary" fontStyle="italic">
                            N/A
                          </Typography>
                        )}
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2">
                          {scan.started_at ? `${formatDistanceToNow(new Date(scan.started_at))} ago` : 'N/A'}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Box sx={{ display: 'flex', gap: 0.5 }}>
                          {scan.deep_scan === 'true' && (
                            <Chip
                              label="Deep"
                              size="small"
                              color="info"
                              icon={<ScannerIcon fontSize="small" />}
                              sx={{ borderRadius: 16 }}
                            />
                          )}
                          <Chip
                            label="Standard"
                            size="small"
                            color="default"
                            sx={{ borderRadius: 16 }}
                          />
                        </Box>
                      </TableCell>
                      <TableCell>
                        {scan.status === 'running' && (
                          <Tooltip title="Stop this scan">
                            <IconButton
                              size="small"
                              color="error"
                              onClick={() => stopScanMutation.mutate()}
                              disabled={stopScanMutation.isPending}
                              sx={{
                                borderRadius: 2,
                                backgroundColor: 'action.hover',
                                '&:hover': {
                                  backgroundColor: 'error.light',
                                  color: 'error.contrastText',
                                }
                              }}
                            >
                              <StopIcon />
                            </IconButton>
                          </Tooltip>
                        )}
                        {scan.error && (
                          <Tooltip title={scan.error}>
                            <IconButton
                              size="small"
                              color="error"
                              sx={{
                                borderRadius: 2,
                                backgroundColor: 'action.hover',
                                '&:hover': {
                                  backgroundColor: 'error.light',
                                  color: 'error.contrastText',
                                }
                              }}
                            >
                              <InfoIcon />
                            </IconButton>
                          </Tooltip>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          ) : (
            <Box sx={{ textAlign: 'center', py: 6 }}>
              <ScannerIcon sx={{ fontSize: 72, color: 'action.disabled', mb: 2 }} />
              <Typography variant="h6" color="text.secondary" gutterBottom>
                No Scan Operations Yet
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
                Start your first scan to begin indexing your media collection
              </Typography>
              <Button
                variant="contained"
                startIcon={<PlayIcon />}
                onClick={handleStartScanDialog}
                sx={{
                  borderRadius: 2,
                  px: 3,
                  py: 1.2,
                  fontWeight: 500,
                  boxShadow: '0px 2px 8px rgba(0, 0, 0, 0.1)',
                  '&:hover': {
                    boxShadow: '0px 4px 12px rgba(0, 0, 0, 0.15)',
                  }
                }}
              >
                Start Your First Scan
              </Button>
            </Box>
          )}
        </CardContent>
      </Card>

      {/* Start Scan Dialog */}
      <Dialog open={startScanDialogOpen} onClose={handleCloseScanDialog} maxWidth="sm" fullWidth>
        <form onSubmit={handleSubmit(handleSubmitScan)}>
          <DialogTitle sx={{ pb: 2, fontWeight: 600 }}>
            Start New Scan
          </DialogTitle>
          <DialogContent dividers sx={{ pt: 2 }}>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
              <Alert
                severity="info"
                sx={{
                  borderRadius: 2,
                  backgroundColor: 'info.light',
                  color: 'info.contrastText',
                }}
              >
                Leave path empty to scan all configured library paths, or specify a specific path to scan.
              </Alert>

              <Controller
                name="path"
                control={control}
                render={({ field }) => (
                  <TextField
                    {...field}
                    label="Specific Path (Optional)"
                    fullWidth
                    helperText="Leave empty to scan all library paths"
                    placeholder="/media/music/new-albums"
                    variant="outlined"
                    InputProps={{
                      startAdornment: (
                        <Box sx={{ display: 'flex', alignItems: 'center', mr: 1 }}>
                          <FolderIcon sx={{ color: 'action.active', mr: 0.5 }} />
                        </Box>
                      ),
                    }}
                  />
                )}
              />

              <Controller
                name="deep_scan"
                control={control}
                render={({ field }) => (
                  <FormControlLabel
                    control={<Switch {...field} checked={field.value} />}
                    label="Deep Scan"
                    sx={{
                      backgroundColor: watchedDeepScan ? 'warning.light' : 'action.hover',
                      borderRadius: 2,
                      px: 2,
                      py: 1,
                      '& .MuiTypography-root': {
                        fontWeight: 500,
                      }
                    }}
                  />
                )}
              />

              <Alert
                severity="warning"
                sx={{
                  borderRadius: 2,
                  backgroundColor: 'warning.light',
                  color: 'warning.contrastText',
                }}
              >
                Deep scan will re-process all files, even if they've been scanned before.
                This is useful after updating metadata extraction rules but takes longer.
              </Alert>

              {watchedPath && (
                <Alert
                  severity="info"
                  sx={{
                    borderRadius: 2,
                    backgroundColor: 'info.light',
                    color: 'info.contrastText',
                  }}
                >
                  Scanning specific path: <code style={{ backgroundColor: 'rgba(255,255,255,0.2)', padding: '2px 4px', borderRadius: '4px' }}>{watchedPath}</code>
                </Alert>
              )}
            </Box>
          </DialogContent>
          <DialogActions sx={{ p: 3, gap: 1 }}>
            <Button
              onClick={handleCloseScanDialog}
              variant="outlined"
              sx={{ borderRadius: 2, px: 3, py: 1 }}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="contained"
              disabled={startScanMutation.isPending}
              startIcon={<PlayIcon />}
              sx={{
                borderRadius: 2,
                px: 3,
                py: 1,
                fontWeight: 500,
                boxShadow: '0px 2px 8px rgba(0, 0, 0, 0.1)',
                '&:hover': {
                  boxShadow: '0px 4px 12px rgba(0, 0, 0, 0.15)',
                }
              }}
            >
              Start Scan
            </Button>
          </DialogActions>
        </form>
      </Dialog>
    </Box>
  )
}

export default Scanning