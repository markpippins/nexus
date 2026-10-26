import React, { useState } from 'react'
import {
  Box,
  Typography,
  Card,
  CardContent,
  Button,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Chip,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  FormControlLabel,
  Switch,
  Alert,
  Tooltip,
  CardHeader,
  Avatar,
  Grid,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Divider,
} from '@mui/material'
import {
  Add as AddIcon,
  Edit as EditIcon,
  Delete as DeleteIcon,
  Folder as FolderIcon,
  Scanner as ScannerIcon,
  Settings as SettingsIcon,
  ExpandMore as ExpandMoreIcon,
  CheckCircle as CheckCircleIcon,
  Cancel as CancelIcon,
  Visibility as VisibilityIcon,
  VisibilityOff as VisibilityOffIcon,
} from '@mui/icons-material'
import { useForm, Controller } from 'react-hook-form'
import { standardSchemaResolver } from '@hookform/resolvers/standard-schema'
import { z } from 'zod'
import { useLibraries, useCreateLibrary, useUpdateLibrary, useDeleteLibrary } from '@/hooks/useApi'
import type { LibraryPath, CreateLibraryPathForm } from '@/types/api'

const librarySchema = z.object({
  path: z.string().min(1, 'Path is required'),
  name: z.string().optional(),
  scan_enabled: z.boolean().default(true),
  deep_scan: z.boolean().default(false),
  path_type: z.enum(['album', 'compilation', 'recent', 'general']).default('general'),
  auto_delete_duplicates: z.boolean().default(false),
  delete_lower_quality: z.boolean().default(true),
  quality_threshold: z.number().min(0).max(1000).default(100),
  preferred_formats: z.string().default('FLAC,MP3'),
  deletion_priority: z.number().min(0).max(100).default(50),
})

type LibraryFormData = z.infer<typeof librarySchema>

const Libraries: React.FC = () => {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingLibrary, setEditingLibrary] = useState<LibraryPath | null>(null)
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [libraryToDelete, setLibraryToDelete] = useState<LibraryPath | null>(null)
  const [showAdvanced, setShowAdvanced] = useState(false)

  const { data: libraries, isLoading, error } = useLibraries()
  const createLibraryMutation = useCreateLibrary()
  const updateLibraryMutation = useUpdateLibrary()
  const deleteLibraryMutation = useDeleteLibrary()

  const { control, handleSubmit, reset, formState: { errors }, watch } = useForm<LibraryFormData>({
    resolver: standardSchemaResolver(librarySchema),
    defaultValues: {
      scan_enabled: true,
      deep_scan: false,
      path_type: 'general',
      auto_delete_duplicates: false,
      delete_lower_quality: true,
      quality_threshold: 100,
      preferred_formats: 'FLAC,MP3',
      deletion_priority: 50,
    },
  })

  const watchedPath = watch('path')
  const watchedName = watch('name')
  const watchedScanEnabled = watch('scan_enabled')
  const watchedDeepScan = watch('deep_scan')
  const watchedAutoDelete = watch('auto_delete_duplicates')

  const handleOpenDialog = (library?: LibraryPath) => {
    if (library) {
      setEditingLibrary(library)
      reset({
        path: library.path,
        name: library.name || '',
        scan_enabled: library.scan_enabled,
        deep_scan: library.deep_scan,
        path_type: library.path_type,
        auto_delete_duplicates: library.auto_delete_duplicates,
        delete_lower_quality: library.delete_lower_quality,
        quality_threshold: library.quality_threshold,
        preferred_formats: library.preferred_formats,
        deletion_priority: library.deletion_priority,
      })
    } else {
      setEditingLibrary(null)
      reset()
    }
    setDialogOpen(true)
  }

  const handleCloseDialog = () => {
    setDialogOpen(false)
    setEditingLibrary(null)
    reset()
    setShowAdvanced(false)
  }

  const handleSubmitForm = (data: LibraryFormData) => {
    if (editingLibrary) {
      updateLibraryMutation.mutate(
        { id: editingLibrary.id, data },
        {
          onSuccess: () => {
            handleCloseDialog()
          },
        }
      )
    } else {
      createLibraryMutation.mutate(data, {
        onSuccess: () => {
          handleCloseDialog()
        },
      })
    }
  }

  const handleDeleteClick = (library: LibraryPath) => {
    setLibraryToDelete(library)
    setDeleteConfirmOpen(true)
  }

  const handleConfirmDelete = () => {
    if (libraryToDelete) {
      deleteLibraryMutation.mutate(libraryToDelete.id, {
        onSuccess: () => {
          setDeleteConfirmOpen(false)
          setLibraryToDelete(null)
        },
      })
    }
  }

  const getPathTypeColor = (type: string) => {
    switch (type) {
      case 'album': return 'primary'
      case 'compilation': return 'secondary'
      case 'recent': return 'warning'
      default: return 'default'
    }
  }

  const getPriorityColor = (priority: number) => {
    if (priority >= 80) return 'error'
    if (priority >= 60) return 'warning'
    return 'success'
  }

  if (error) {
    return (
      <Alert severity="error" sx={{ borderRadius: 2 }}>
        Failed to load library paths. Please check your connection and try again.
      </Alert>
    )
  }

  return (
    <Box>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
        <Typography variant="h4" sx={{ fontWeight: 600 }}>
          Library Paths
        </Typography>
        <Button
          variant="contained"
          startIcon={<AddIcon />}
          onClick={() => handleOpenDialog()}
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
          Add Library Path
        </Button>
      </Box>

      <Card sx={{ borderRadius: 3, overflow: 'hidden' }}>
        <CardHeader
          avatar={
            <Avatar sx={{ bgcolor: 'primary.main' }}>
              <FolderIcon />
            </Avatar>
          }
          title={
            <Typography variant="h6" sx={{ fontWeight: 600 }}>
              Configured Paths
            </Typography>
          }
          sx={{ pb: 1 }}
        />
        <CardContent sx={{ pt: 0 }}>
          {isLoading ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', py: 4 }}>
              <Typography>Loading library paths...</Typography>
            </Box>
          ) : libraries && libraries.length > 0 ? (
            <TableContainer component={Paper} variant="outlined" sx={{ borderRadius: 2, boxShadow: 'none' }}>
              <Table>
                <TableHead>
                  <TableRow sx={{ backgroundColor: 'background.paper' }}>
                    <TableCell>Path</TableCell>
                    <TableCell>Name</TableCell>
                    <TableCell>Type</TableCell>
                    <TableCell>Scan Settings</TableCell>
                    <TableCell>Duplicate Rules</TableCell>
                    <TableCell>Actions</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {libraries.map((library) => (
                    <TableRow
                      key={library.id}
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
                        <Box sx={{ display: 'flex', alignItems: 'center' }}>
                          <FolderIcon sx={{ mr: 1, fontSize: 18, color: 'primary.main' }} />
                          <Typography
                            variant="body2"
                            sx={{
                              fontFamily: 'monospace',
                              wordBreak: 'break-all',
                              maxWidth: '200px'
                            }}
                          >
                            {library.path}
                          </Typography>
                        </Box>
                      </TableCell>
                      <TableCell>
                        {library.name || (
                          <Typography variant="body2" color="text.secondary" fontStyle="italic">
                            No name
                          </Typography>
                        )}
                      </TableCell>
                      <TableCell>
                        <Chip
                          label={library.path_type}
                          size="small"
                          color={getPathTypeColor(library.path_type)}
                          sx={{ borderRadius: 16 }}
                        />
                      </TableCell>
                      <TableCell>
                        <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                          <Chip
                            label={library.scan_enabled ? 'Enabled' : 'Disabled'}
                            size="small"
                            color={library.scan_enabled ? 'success' : 'default'}
                            icon={library.scan_enabled ? <CheckCircleIcon fontSize="small" /> : <CancelIcon fontSize="small" />}
                            sx={{ borderRadius: 16 }}
                          />
                          {library.deep_scan && (
                            <Chip
                              label="Deep"
                              size="small"
                              color="info"
                              icon={<ScannerIcon fontSize="small" />}
                              sx={{ borderRadius: 16 }}
                            />
                          )}
                        </Box>
                      </TableCell>
                      <TableCell>
                        <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                          {library.auto_delete_duplicates && (
                            <Chip
                              label="Auto Delete"
                              size="small"
                              color="warning"
                              icon={<VisibilityOffIcon fontSize="small" />}
                              sx={{ borderRadius: 16 }}
                            />
                          )}
                          <Tooltip title={`Deletion Priority: ${library.deletion_priority}`}>
                            <Chip
                              label={`P${library.deletion_priority}`}
                              size="small"
                              color={getPriorityColor(library.deletion_priority)}
                              sx={{ borderRadius: 16 }}
                            />
                          </Tooltip>
                        </Box>
                      </TableCell>
                      <TableCell>
                        <Box sx={{ display: 'flex', gap: 1 }}>
                          <IconButton
                            size="small"
                            onClick={() => handleOpenDialog(library)}
                            color="primary"
                            sx={{
                              borderRadius: 2,
                              backgroundColor: 'action.hover',
                              '&:hover': {
                                backgroundColor: 'primary.light',
                                color: 'primary.contrastText',
                              }
                            }}
                          >
                            <EditIcon />
                          </IconButton>
                          <IconButton
                            size="small"
                            onClick={() => handleDeleteClick(library)}
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
                            <DeleteIcon />
                          </IconButton>
                        </Box>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          ) : (
            <Box sx={{ textAlign: 'center', py: 6 }}>
              <FolderIcon sx={{ fontSize: 72, color: 'action.disabled', mb: 2 }} />
              <Typography variant="h6" color="text.secondary" gutterBottom>
                No Library Paths Configured
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
                Add library paths to start scanning your media collection
              </Typography>
              <Button
                variant="contained"
                startIcon={<AddIcon />}
                onClick={() => handleOpenDialog()}
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
                Add Your First Library Path
              </Button>
            </Box>
          )}
        </CardContent>
      </Card>

      {/* Add/Edit Dialog */}
      <Dialog open={dialogOpen} onClose={handleCloseDialog} maxWidth="md" fullWidth sx={{ borderRadius: 3 }}>
        <form onSubmit={handleSubmit(handleSubmitForm)}>
          <DialogTitle sx={{ pb: 2, fontWeight: 600 }}>
            {editingLibrary ? 'Edit Library Path' : 'Add Library Path'}
          </DialogTitle>
          <DialogContent dividers sx={{ pt: 2 }}>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
              <Controller
                name="path"
                control={control}
                render={({ field }) => (
                  <TextField
                    {...field}
                    label="Path"
                    fullWidth
                    error={!!errors.path}
                    helperText={errors.path?.message || 'Absolute path to the directory (e.g., /media/music)'}
                    placeholder="/media/music"
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
                name="name"
                control={control}
                render={({ field }) => (
                  <TextField
                    {...field}
                    label="Name (Optional)"
                    fullWidth
                    helperText="Friendly name for this library path"
                    placeholder="Music Collection"
                    variant="outlined"
                  />
                )}
              />

              <Grid container spacing={2}>
                <Grid item xs={6}>
                  <Controller
                    name="path_type"
                    control={control}
                    render={({ field }) => (
                      <FormControl fullWidth>
                        <InputLabel>Path Type</InputLabel>
                        <Select
                          {...field}
                          label="Path Type"
                          variant="outlined"
                        >
                          <MenuItem value="album">Album</MenuItem>
                          <MenuItem value="compilation">Compilation</MenuItem>
                          <MenuItem value="recent">Recent</MenuItem>
                          <MenuItem value="general">General</MenuItem>
                        </Select>
                      </FormControl>
                    )}
                  />
                </Grid>

                <Grid item xs={6}>
                  <Controller
                    name="deletion_priority"
                    control={control}
                    render={({ field }) => (
                      <TextField
                        {...field}
                        label="Deletion Priority"
                        type="number"
                        fullWidth
                        error={!!errors.deletion_priority}
                        helperText={errors.deletion_priority?.message || 'Higher = more likely to be deleted (0-100)'}
                        inputProps={{ min: 0, max: 100 }}
                        variant="outlined"
                      />
                    )}
                  />
                </Grid>
              </Grid>

              <Box sx={{ display: 'flex', gap: 2 }}>
                <Controller
                  name="scan_enabled"
                  control={control}
                  render={({ field }) => (
                    <FormControlLabel
                      control={<Switch {...field} checked={field.value} />}
                      label="Enable Scanning"
                      sx={{
                        backgroundColor: watchedScanEnabled ? 'success.light' : 'error.light',
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

                <Controller
                  name="deep_scan"
                  control={control}
                  render={({ field }) => (
                    <FormControlLabel
                      control={<Switch {...field} checked={field.value} />}
                      label="Deep Scan"
                      sx={{
                        backgroundColor: watchedDeepScan ? 'info.light' : 'action.hover',
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
              </Box>

              <Accordion
                expanded={showAdvanced}
                onChange={() => setShowAdvanced(!showAdvanced)}
                sx={{
                  borderRadius: 2,
                  boxShadow: '0px 2px 6px rgba(0, 0, 0, 0.08)',
                  '&.Mui-expanded': {
                    margin: 0,
                    boxShadow: '0px 4px 12px rgba(0, 0, 0, 0.12)',
                  }
                }}
              >
                <AccordionSummary
                  expandIcon={<ExpandMoreIcon />}
                  sx={{
                    borderRadius: 2,
                    '& .MuiAccordionSummary-content': {
                      margin: '12px 0',
                    }
                  }}
                >
                  <Typography variant="subtitle2" sx={{ fontWeight: 600, display: 'flex', alignItems: 'center' }}>
                    <SettingsIcon sx={{ mr: 1, color: 'primary.main' }} />
                    Advanced Settings
                  </Typography>
                </AccordionSummary>
                <AccordionDetails>
                  <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                    <Box sx={{ display: 'flex', gap: 2 }}>
                      <Controller
                        name="auto_delete_duplicates"
                        control={control}
                        render={({ field }) => (
                          <FormControlLabel
                            control={<Switch {...field} checked={field.value} />}
                            label="Auto Delete Duplicates"
                            sx={{
                              backgroundColor: watchedAutoDelete ? 'warning.light' : 'action.hover',
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

                      <Controller
                        name="delete_lower_quality"
                        control={control}
                        render={({ field }) => (
                          <FormControlLabel
                            control={<Switch {...field} checked={field.value} />}
                            label="Delete Lower Quality"
                            sx={{
                              backgroundColor: field.value ? 'warning.light' : 'action.hover',
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
                    </Box>

                    <Grid container spacing={2}>
                      <Grid item xs={6}>
                        <Controller
                          name="quality_threshold"
                          control={control}
                          render={({ field }) => (
                            <TextField
                              {...field}
                              label="Quality Threshold"
                              type="number"
                              fullWidth
                              error={!!errors.quality_threshold}
                              helperText={errors.quality_threshold?.message || 'Minimum quality difference to trigger deletion'}
                              inputProps={{ min: 0, max: 1000 }}
                              variant="outlined"
                            />
                          )}
                        />
                      </Grid>

                      <Grid item xs={6}>
                        <Controller
                          name="preferred_formats"
                          control={control}
                          render={({ field }) => (
                            <TextField
                              {...field}
                              label="Preferred Formats"
                              fullWidth
                              helperText="Comma-separated list of preferred formats (e.g., FLAC,MP3)"
                              placeholder="FLAC,MP3,OGG"
                              variant="outlined"
                            />
                          )}
                        />
                      </Grid>
                    </Grid>
                  </Box>
                </AccordionDetails>
              </Accordion>
            </Box>
          </DialogContent>
          <DialogActions sx={{ p: 3, gap: 1 }}>
            <Button
              onClick={handleCloseDialog}
              variant="outlined"
              sx={{ borderRadius: 2, px: 3, py: 1 }}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="contained"
              disabled={createLibraryMutation.isPending || updateLibraryMutation.isPending}
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
              {editingLibrary ? 'Update' : 'Create'}
            </Button>
          </DialogActions>
        </form>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog open={deleteConfirmOpen} onClose={() => setDeleteConfirmOpen(false)}>
        <DialogTitle sx={{ fontWeight: 600 }}>Confirm Deletion</DialogTitle>
        <DialogContent>
          <Typography>
            Are you sure you want to delete the library path "{libraryToDelete?.path}"?
            This will not delete the actual files, only remove it from the scanning configuration.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ p: 3, gap: 1 }}>
          <Button
            onClick={() => setDeleteConfirmOpen(false)}
            variant="outlined"
            sx={{ borderRadius: 2, px: 3, py: 1 }}
          >
            Cancel
          </Button>
          <Button
            onClick={handleConfirmDelete}
            color="error"
            variant="contained"
            disabled={deleteLibraryMutation.isPending}
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
            Delete
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

export default Libraries